import json
import os

import pytest

from backend.rag.store import MemoryStore
from ingest import refresh, scraper
from ingest.criteria import diff_criteria, extract_criteria, normalise
from ingest.parse import document_list, md_to_text, parse_raw


def raw_page(slug="pm-kisan", benefits="Financial benefit of **Rs. 6000 per annum** per family.", status="ok",
             level="central", state=None):
    basic = {"schemeName": "Pradhan Mantri Kisan Samman Nidhi", "schemeShortTitle": "PM-KISAN",
             "level": {"label": level.title(), "value": level},
             "nodalMinistryName": {"label": "Ministry Of Agriculture and Farmers Welfare"},
             "schemeCategory": [{"label": "Agriculture,Rural & Environment"}], "tags": ["Farmers", "Kisan"]}
    if state:
        basic["state"] = {"label": state}
    return {
        "slug": slug, "lang": "en", "source_url": f"https://www.myscheme.gov.in/schemes/{slug}",
        "fetched_at": "2026-11-27T00:00:00+00:00", "status": status, "text": "", "links": [],
        "content": {
            "scheme": {"data": {"en": {
                "basicDetails": basic,
                "schemeContent": {"detailedDescription_md": "#### Objective\nIncome support to farmer families.",
                                  "benefits_md": benefits,
                                  "exclusions_md": "- Income tax payers\n- Serving government employees",
                                  "references": [{"title": "Guidelines", "url": "https://pmkisan.gov.in/g.pdf "}]},
                "eligibilityCriteria": {"eligibilityDescription_md":
                                        "All landholding farmers&amp;#39; families are eligible."},
                "applicationProcess": [{"mode": "Online", "process_md": "1. Visit the portal\n2. Register"}],
            }}},
            "documents": {"data": {"en": {"documents_required": [
                {"type": "ol_list", "children": [
                    {"type": "list_item", "children": [{"text": "Aadhaar Card"}]},
                    {"type": "list_item", "children": [{"text": "Landholding papers - original"}]},
                    {"type": "list_item", "children": [{"text": "aadhaar card"}]}]}]}}},
            "faqs": {"data": {"en": {"faqs": [{"question": "Is it for small farmers only",
                                               "answer_md": "No, all landholding farmers."}]}}},
        },
    }


def test_md_to_text_keeps_lists_and_unescapes():
    t = md_to_text("#### Steps\n1. Visit **portal**\n2. Fill [form](https://x.gov.in)\nfarmers&amp;#39; families")
    assert t == "Steps: Visit portal. Fill form (https://x.gov.in). farmers' families."


def test_document_list_from_rich_text_dedupes():
    docs = document_list(raw_page()["content"]["documents"])
    assert docs == ["Aadhaar Card", "Landholding papers"]


def test_parse_raw_fields():
    rec = parse_raw({"en": raw_page()})
    s = rec["scheme"]
    assert s["name"].startswith("Pradhan Mantri") and s["state"] == "ALL" and s["category"] == "agriculture"
    assert s["pdf_url"] == "https://pmkisan.gov.in/g.pdf"
    assert s["documents_required"] == ["Aadhaar Card", "Landholding papers"]
    assert set(rec["sections"]["en"]) >= {"details", "benefits", "eligibility", "exclusions",
                                          "application_process", "documents_required", "faq"}
    state = parse_raw({"en": raw_page(level="state", state="Uttar Pradesh")})["scheme"]
    assert state["state"] == "Uttar Pradesh"
    assert parse_raw({"en": raw_page(status="error")}) is None


def test_criteria_normalise_validates_llm_output():
    c = normalise({"states": ["ALL"], "age": {"min": 18, "max": "x"}, "gender": "women",
                   "occupation": ["Farmer", "astronaut"], "annual_income": {"max": 200000.0},
                   "social_category": "sc", "bpl": "yes", "extra": 1}, "ALL")
    assert c == {"states": ["ALL"], "age": {"min": 18}, "gender": "any", "occupation": ["farmer"],
                 "land_acres": None, "annual_income": {"max": 200000}, "social_category": ["SC"],
                 "disability": None, "bpl": None}
    assert normalise({"states": ["ALL"]}, "Uttar Pradesh")["states"] == ["Uttar Pradesh"]
    assert diff_criteria({"age": {"min": 18}}, {"age": {"min": 21}}) == ["age"]


def test_extract_criteria_offline_uses_regex():
    c = extract_criteria({"eligibility": "Women aged between 18 and 40 years from BPL households."})
    assert c["_extracted_by"] == "regex" and c["age"] == {"min": 18, "max": 40} and c["bpl"] is True


@pytest.fixture()
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, "RAW_DIR", str(tmp_path))
    return tmp_path


def _write(raw_dir, page):
    with open(os.path.join(raw_dir, f"{page['slug']}.en.json"), "w", encoding="utf-8") as fh:
        json.dump(page, fh)


def test_refresh_detects_new_unchanged_updated_and_never_deletes(raw_dir):
    store = MemoryStore()
    _write(raw_dir, raw_page())
    assert refresh.run(["pm-kisan"], store=store, fetch=False, log=lambda *_: None)["added"] == 1
    assert refresh.run(["pm-kisan"], store=store, fetch=False, log=lambda *_: None)["unchanged"] == 1

    store.set_verification("pm-kisan", "verified")
    _write(raw_dir, raw_page(benefits="Financial benefit of Rs. 8000 per annum."))
    out = refresh.run(["pm-kisan"], store=store, fetch=False, log=lambda *_: None)
    assert out["updated"] == 1
    s = store.get_scheme("pm-kisan")
    assert s["verification"] == "stale"            # changed after hand-verification
    assert len(store.versions) == 1                 # old copy kept
    assert "1 updated, 0 new" in store.recent_updates(1)[0]["details"]["summary"]

    _write(raw_dir, raw_page(status="not_found"))
    assert refresh.run(["pm-kisan"], store=store, fetch=False, log=lambda *_: None)["inactive"] == 1
    assert store.get_scheme("pm-kisan")["status"] == "inactive"  # kept, not deleted


def test_refresh_keeps_scheme_on_transient_fetch_error(raw_dir):
    store = MemoryStore()
    _write(raw_dir, raw_page())
    refresh.run(["pm-kisan"], store=store, fetch=False, log=lambda *_: None)
    _write(raw_dir, raw_page(status="error"))
    out = refresh.run(["pm-kisan"], store=store, fetch=False, log=lambda *_: None)
    assert out["inactive"] == 0 and store.get_scheme("pm-kisan")["status"] == "active"


def test_mark_stale_after_30_days():
    from datetime import datetime, timedelta, timezone
    store = MemoryStore()
    store.upsert_scheme({"slug": "old", "name": "Old scheme", "content_hash": "h"})
    store.schemes[1]["last_checked"] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    assert refresh.mark_stale(store) == ["old"] and store.get_scheme("old")["verification"] == "stale"


def test_allowlist():
    assert scraper.allowed_url("https://www.myscheme.gov.in/schemes/x")
    assert not scraper.allowed_url("https://evil.example.com/x")
    assert scraper.allowed_url("https://pmkisan.gov.in/a.pdf", fetch=False)
