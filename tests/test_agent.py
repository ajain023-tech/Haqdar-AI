from backend.agent.flow import Agent, AgentConfig
from backend.agent.router import SchemeIndex, named_phrase, route
from backend.core.lang import detect_lang
from backend.rules import evaluate, extract_profile


def test_detect_lang():
    assert detect_lang("पीएम किसान में कितने पैसे मिलते हैं?") == "hi"
    assert detect_lang("PM Kisan mein kitne paise milte hain?") == "hinglish"
    assert detect_lang("How much money does PM Kisan give?") == "en"


def test_profile_extraction_does_not_confuse_stand_up_with_up():
    assert "state" not in extract_profile("How to apply for Stand-Up India loan?")
    p = extract_profile("Main UP ka 45 saal ka kisan hoon, 2 acre zameen, income 1.2 lakh")
    assert p["state"] == "Uttar Pradesh" and p["age"] == 45 and p["occupation"] == "farmer"
    assert p["land_acres"] == 2.0 and p["annual_income"] == 120000


def test_profile_extraction_hindi_digits():
    p = extract_profile("मेरी उम्र ३५ साल है, मैं विधवा हूँ, उत्तर प्रदेश")
    assert p["age"] == 35 and p["gender"] == "female" and p["state"] == "Uttar Pradesh"


def test_router_resolves_aliases_and_flags_unknown_names(store):
    idx = SchemeIndex(store)
    r = route("उज्ज्वला योजना में क्या मिलता है?", idx)
    assert r.intent == "scheme_question" and r.scheme_ids == [store.by_slug["pmuy"]]
    r = route("What is PM Free Smartphone Yojana?", idx)
    assert r.named and not r.named_resolved
    assert route("I am a 30 year old farmer, which schemes can I get?", idx).intent == "find_schemes"
    assert named_phrase("kaun si yojana milegi") is None


def test_rule_engine_verdicts(store):
    schemes = store.list_schemes()
    verdicts = {v.slug: v for v in evaluate({"state": "Uttar Pradesh", "gender": "male", "age": 30}, schemes)}
    assert verdicts["pmuy"].verdict == "no"
    assert verdicts["pm-kisan"].verdict == "maybe" and "occupation" in verdicts["pm-kisan"].missing


def test_trick_question_is_refused(store):
    out = Agent(AgentConfig(use_cache=False)).run("How do I apply for the PM Gold Coin Yojana?")
    assert out["answer"]["refused"]
    assert out["answer"]["citations"] == []


def test_named_question_answers_with_citation_and_card(store):
    out = Agent(AgentConfig(use_cache=False)).run("PM Kisan mein saal mein kitne paise milte hain?")
    assert out["lang"] == "hinglish" and not out["answer"]["refused"]
    assert out["answer"]["citations"][0]["slug"] == "pm-kisan"
    assert out["answer"]["citations"][0]["source_url"].startswith("https://www.myscheme.gov.in/")
    assert out["schemes"][0]["slug"] == "pm-kisan"
    assert "disclaimer" in out["answer"]


def test_find_schemes_asks_follow_up_then_uses_answer(store):
    agent = Agent(AgentConfig(use_cache=False))
    first = agent.run("I am a 45 year old widow in UP, which schemes can I get?")
    assert first["intent"] == "find_schemes"
    assert first["follow_up"]["field"] == "bpl"
    cards = {c["slug"]: c["verdict"] for c in first["schemes"]}
    assert cards.get("ignwps") == "maybe"

    second = agent.run("yes, we have a BPL card", session_id=first["session_id"])
    assert second["profile"]["bpl"] is True and second["profile"]["age"] == 45  # profile kept in the session
    cards = {c["slug"]: c["verdict"] for c in second["schemes"]}
    assert cards.get("ignwps") == "eligible"


def test_stream_events_order_and_cache(store):
    agent = Agent(AgentConfig())
    events = list(agent.events("Ujjwala yojana ke documents kya hain?"))
    types = [e["type"] for e in events]
    assert types[0] == "meta" and types[-1] == "final" and "token" in types
    again = agent.run("Ujjwala yojana ke documents kya hain?")
    assert again["cached"] is True
    assert again["answer"]["text"] == events[-1]["answer"]["text"]


def test_named_phrase_ignores_generic_hindi_lead_words():
    assert named_phrase("छोटे किसानों को सरकार हर साल 6000 रुपये किस योजना में देती है?") is None
    assert named_phrase("100 din ka rozgar wali yojana batao") is None
    assert named_phrase("Is there a government pension scheme for workers?") is None
    assert named_phrase("पीएम फ्री लैपटॉप योजना 2026 की पात्रता") == "पीएम फ्री लैपटॉप योजना"
    assert named_phrase("How do I apply for the PM Mobile Recharge Yojana?") == "PM Mobile Recharge Yojana"


def test_partial_name_overlap_is_still_refused(store):
    # "Bima" alone matches real insurance schemes; 1 of 3 distinctive words is not enough.
    out = Agent(AgentConfig(use_cache=False)).run("What documents are required for the Digital Gaay Bima Yojana?")
    assert out["answer"]["refused"]


def test_hindi_question_found_via_english_rewrite(store):
    out = Agent(AgentConfig(use_cache=False)).run("पति की मृत्यु के बाद गरीब महिला को पेंशन कैसे मिलेगी?")
    assert out["retrieval"]["hits"][0]["slug"] == "ignwps"


def test_reasons_in_user_language(store):
    schemes = [s for s in store.list_schemes() if s["slug"] == "pmuy"]
    v = evaluate({"gender": "male", "age": 30, "bpl": True}, schemes, "hi")[0]
    assert v.verdict == "no" and "महिला" in v.reasons[0]


def test_followup_prefers_field_that_resolves_most(store):
    from backend.rules import pick_followup
    from backend.rules.engine import Verdict
    vs = [Verdict(1, "a", "A", "maybe", missing=["bpl"]), Verdict(2, "b", "B", "maybe", missing=["bpl"]),
          Verdict(3, "c", "C", "maybe", missing=["age", "gender", "occupation"])]
    fu = pick_followup(vs, "hinglish")
    assert fu["field"] == "bpl" and fu["options"] == ["yes", "no"] and fu["affects"] == 2
    assert pick_followup(vs, "en", asked=["bpl"])["field"] == "age"


def test_short_answer_fills_pending_field():
    assert extract_profile("नहीं", {"age": 40}, pending="bpl") == {"age": 40, "bpl": False}
    assert extract_profile("haan ji", pending="disability") == {"disability": True}
    assert extract_profile("mahine ki kamai 8000")["annual_income"] == 96000
