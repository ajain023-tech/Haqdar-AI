from backend.rag.answer import REFUSAL, extractive_answer, finalize, generate
from backend.rag.chunking import chunk_scheme, chunk_section, estimate_tokens
from backend.rag.indexer import content_hash, index_scheme
from backend.rag.search import reciprocal_rank_fusion, retrieve
from backend.rag.selfcheck import check, split_claims, strip_unsupported
from backend.rag.store import Filters, Hit


def _hit(cid, sid=1, score=0.0, text="t", slug="s"):
    return Hit(chunk_id=cid, scheme_id=sid, slug=slug, name=slug, section="details", lang="en", text=text, score=score)


# ---------------------------------------------------------------- chunking
def test_chunks_respect_target_and_overlap():
    text = " ".join(f"Sentence number {i} talks about farmers and their land records." for i in range(200))
    chunks = chunk_section(text, target=100, overlap=20)
    assert len(chunks) > 5
    assert all(estimate_tokens(c) <= 130 for c in chunks)
    # overlap: the last sentence of chunk k reappears at the start of chunk k+1
    last_sentence = chunks[0].split(". ")[-1].rstrip(".")
    assert last_sentence in chunks[1]


def test_chunks_never_cross_sections_and_are_labelled():
    chunks = chunk_scheme("PM-KISAN", {"benefits": "Rs 6000 per year.", "eligibility": "Landholding farmers."})
    assert [c.section for c in chunks] == ["benefits", "eligibility"]
    assert chunks[0].text.startswith("PM-KISAN | Benefits:")


def test_hindi_sentences_split_on_danda():
    text = "यह योजना किसानों के लिए है। " * 300
    chunks = chunk_section(text, target=200, overlap=20)
    assert len(chunks) > 1
    assert all(c.endswith("।") for c in chunks)


# ---------------------------------------------------------------- fusion / search
def test_rrf_rewards_agreement():
    a = [_hit(1), _hit(2), _hit(3)]
    b = [_hit(3), _hit(1), _hit(4)]
    fused = reciprocal_rank_fusion(a, b)
    assert [h.chunk_id for h in fused][:2] == [1, 3]
    assert fused[0].score > fused[-1].score


def test_retrieve_modes_find_the_right_scheme(store):
    for mode in ("vector", "hybrid", "rerank"):
        res = retrieve("Ujjwala LPG connection for women", mode=mode, store=store)
        assert res.hits and res.hits[0].slug == "pmuy", mode
        assert len(res.hits) <= 5


def test_state_filter_hides_other_states_schemes(store):
    res = retrieve("girl child Rs 25000", mode="hybrid", store=store, filters=Filters(state="Bihar"))
    assert all(h.slug != "ksy" for h in res.hits)
    res = retrieve("girl child Rs 25000", mode="hybrid", store=store, filters=Filters(state="Uttar Pradesh"))
    assert res.hits[0].slug == "ksy"


def test_incremental_reindex_skips_unchanged(store):
    scheme = {"slug": "pm-kisan", "name": "Pradhan Mantri Kisan Samman Nidhi"}
    sections = {"en": {"details": "PM-KISAN is a central sector scheme that provides income support to all "
                                  "landholding farmer families."}}
    first = index_scheme(dict(scheme, content_hash=content_hash(sections)), sections, store=store)
    second = index_scheme(dict(scheme, content_hash=content_hash(sections)), sections, store=store)
    assert first == "updated" and second == "unchanged"
    assert len(store.versions) == 1  # old copy kept in scheme_versions


# ---------------------------------------------------------------- answers
def test_refuses_below_threshold():
    ans = generate("anything", [_hit(1, score=0.05, text="x")], "hi", top_score=0.05, threshold=0.15)
    assert ans.refused and ans.text == REFUSAL["hi"]


def test_finalize_keeps_only_used_in_range_citations():
    hits = [_hit(1, slug="a"), _hit(2, slug="b")]
    ans = finalize("Fact one [1]. Fact two [7].", hits, "en")
    assert [c.n for c in ans.citations] == [1]
    assert "[7]" not in ans.text


def test_extractive_answer_cites_sources(store):
    res = retrieve("How much money does PM Kisan give per year?", store=store)
    text = extractive_answer("How much money does PM Kisan give per year?", res.hits, "en")
    assert "[1]" in text and "6000" in text


# ---------------------------------------------------------------- self-check
def test_selfcheck_flags_unsupported_and_wrong_numbers():
    hits = [_hit(1, text="Farmers get Rs 6000 per year in three instalments.")]
    good = check("Farmers get Rs 6000 per year in three instalments [1].", hits)
    assert good.ok and good.faithfulness == 1.0
    wrong_amount = check("Farmers get Rs 9000 per year in three instalments [1].", hits)
    assert not wrong_amount.ok
    uncited = check("Farmers also get a free tractor every year.", hits)
    assert not uncited.ok and uncited.claims[0].reason == "no citation"


def test_split_claims_keeps_citations_with_their_sentence():
    claims = split_claims("Rs 6000 a year is paid. [1] It comes in three instalments. [1][2]")
    assert claims == ["Rs 6000 a year is paid. [1]", "It comes in three instalments. [1][2]"]


def test_strip_unsupported_removes_bad_claims():
    hits = [_hit(1, text="Farmers get Rs 6000 per year.")]
    text = "Farmers get Rs 6000 per year [1]. Everyone gets a free car instantly [1]."
    res = check(text, hits)
    cleaned = strip_unsupported(text, res)
    assert "free car" not in cleaned and "6000" in cleaned


def test_threshold_zero_never_refuses_on_score():
    from backend.rag.answer import should_refuse
    assert not should_refuse([_hit(1)], top_score=-0.02, threshold=0.0)
    assert should_refuse([], top_score=1.0, threshold=0.0)


def test_glossary_rewrite():
    from backend.rag.query import glossary_rewrite, rewrite_query
    assert "widow" in glossary_rewrite("विधवा पेंशन कितनी है?") and "pension" in glossary_rewrite("विधवा पेंशन")
    assert rewrite_query("What is PM Kisan?", "en") is None
