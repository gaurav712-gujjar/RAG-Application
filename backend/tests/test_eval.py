import json

import pytest

from app.embeddings.hashing import HashingEmbedder
from app.llm.prompts import NOT_FOUND
from evals.dataset import GOLDEN, doc_paths, load_qa
from evals.metrics import (
    GenerationResult, RetrievalResult, check_thresholds, evidence_rank, summarize_generation, summarize_retrieval,
)
from evals.pipeline import build_index, eval_settings, run_generation, run_retrieval
from evals.ragas_judge import to_ragas_rows
from evals.run_eval import main, parse_minimums
from tests.fakes import FakeLLM


# ---------------- golden set integrity (stops the dataset rotting) ----------------
def test_golden_set_is_consistent_with_the_documents():
    docs = {p.name: p.read_text(encoding="utf-8") for p in doc_paths()}
    qa = load_qa()
    assert len(docs) >= 4 and len(qa) >= 20
    assert len({q.id for q in qa}) == len(qa)  # unique ids
    for q in qa:
        if q.answerable:
            assert q.source in docs, f"{q.id}: unknown source {q.source}"
            assert q.evidence and q.evidence in docs[q.source], f"{q.id}: evidence not found in {q.source}"
            assert q.key_fact and q.ground_truth
        else:
            assert not q.source and not q.evidence, f"{q.id}: unanswerable question must not point at a source"
            assert q.ground_truth == NOT_FOUND
    assert {q.tag for q in qa} >= {"semantic", "keyword", "hindi", "unanswerable"}
    assert sum(not q.answerable for q in qa) >= 3
    assert {q.source for q in qa if q.answerable} == set(docs), "every golden doc should have at least one question"


# ---------------- metric maths ----------------
def _rr(qa_id, texts, evidence="ANSWER", answerable=True, tag="t"):
    return RetrievalResult(qa_id, tag, answerable, ["f.md"] * len(texts), texts, "f.md", evidence)


def test_evidence_rank_checks_text_not_just_the_file():
    assert evidence_rank(_rr("a", ["noise", "has ANSWER inside", "ANSWER again"])) == 2
    assert evidence_rank(_rr("b", ["right file, wrong passage"])) is None
    assert evidence_rank(_rr("c", [])) is None
    assert evidence_rank(_rr("d", ["ANSWER"], answerable=False)) is None


def test_retrieval_summary_hit_mrr_and_abstain():
    results = [
        _rr("r1", ["ANSWER"]),                      # rank 1
        _rr("r2", ["x", "ANSWER"]),                 # rank 2
        _rr("r3", ["x", "y", "z", "ANSWER"]),       # rank 4: outside k=3
        _rr("r4", ["x"]),                           # miss
        _rr("r5", []),                              # false abstain (also a miss)
        _rr("u1", [], answerable=False),            # correct abstain
        _rr("u2", ["junk"], answerable=False),      # hallucination risk: retrieved something
    ]
    r = summarize_retrieval(results, k=3)
    assert r["n_answerable"] == 5 and r["n_unanswerable"] == 2
    assert r["hit_at_1"] == 0.2 and r["hit_at_3"] == 0.4
    assert r["mrr"] == pytest.approx((1 + 0.5 + 0.25) / 5, abs=1e-4)
    assert r["abstain_rate_unanswerable"] == 0.5 and r["false_abstain_rate_answerable"] == 0.2
    assert r["misses"] == ["r3", "r4", "r5"]


def test_generation_summary():
    g = lambda i, ans, abst, fact="14 days", cited=True, tok=100, ttft=100.0, answerable=True: GenerationResult(
        i, "t", answerable, ans, abst, fact, cited, tok, ttft)
    s = summarize_generation([
        g("a", "Refunds take 14 days [1].", False),
        g("b", "I think it is ten days.", False, cited=False, ttft=300.0),
        g("c", NOT_FOUND, True, tok=0, ttft=None),            # false abstain
        g("u1", NOT_FOUND, True, fact=None, answerable=False, tok=0, ttft=None),
        g("u2", "Sure, it is 5 dollars [1].", False, fact=None, answerable=False),  # hallucinated an answer
    ])
    assert s["fact_in_answer"] == pytest.approx(1 / 3, abs=1e-3)
    assert s["false_abstain_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert s["correct_abstain_rate"] == 0.5
    assert s["citation_rate"] == pytest.approx(1 / 2)  # of the answers that were given for answerable Qs
    assert s["ttft_ms_p50"] in (100.0, 300.0)


def test_threshold_gates_fail_closed():
    report = {"retrieval": {"hit_at_3": 0.8, "mrr": 0.5}, "generation": {"fact_in_answer": 0.9}}
    assert check_thresholds(report, {"hit_at_3": 0.8, "fact_in_answer": 0.9}) == []
    fails = check_thresholds(report, {"hit_at_3": 0.9, "mrr": 0.5, "faithfulness": 0.7})
    assert any(f.startswith("hit_at_3: 0.8 < 0.9") for f in fails)
    assert any("faithfulness: not measured" in f for f in fails)  # an unmeasured gate must not pass silently
    assert not any(f.startswith("mrr") for f in fails)


def test_parse_minimums():
    assert parse_minimums(["hit_at_3=0.9", "mrr = 0.5"]) == {"hit_at_3": 0.9, "mrr": 0.5}
    with pytest.raises(SystemExit):
        parse_minimums(["oops"])


# ---------------- pipeline end to end (hashing embedder, fake LLM) ----------------
async def test_retrieval_and_generation_pipeline_runs_over_the_golden_set():
    settings = eval_settings(relevance_threshold=0.05)
    index = await build_index(HashingEmbedder(), None, settings)
    assert index.n_chunks > len(doc_paths())  # small chunks: every doc is split

    qa = load_qa()
    results, hits = await run_retrieval(index, qa)
    report = summarize_retrieval(results)
    assert report["hit_at_3"] >= 0.7 and report["mrr"] >= 0.6  # weak embedder, but hybrid keeps it decent
    assert report["by_tag"]["hindi"]["hit_at_3"] == 1.0  # Hindi keywords survive the BM25 tokenizer

    llm = FakeLLM(delay=0.0)
    gen, rows = await run_generation(index, llm, qa, hits)
    answered = [g for g in gen if g.answer != NOT_FOUND]
    assert answered and all(g.cited for g in answered)  # FakeLLM always cites [1]
    assert llm.calls == sum(1 for q in qa if hits[q.id])  # the LLM is skipped when nothing was retrieved
    assert len(rows) == len(qa) and all({"question", "answer", "contexts", "ground_truth"} <= set(r) for r in rows)
    assert summarize_generation(gen)["avg_tokens"] > 0


def test_ragas_rows_exclude_unanswerable_and_empty_context_questions():
    rows = [
        {"question": "q1", "answer": "a1", "contexts": ["c"], "ground_truth": "g1", "answerable": True},
        {"question": "q2", "answer": "a2", "contexts": [], "ground_truth": "g2", "answerable": True},    # false abstain
        {"question": "q3", "answer": NOT_FOUND, "contexts": ["c"], "ground_truth": NOT_FOUND, "answerable": False},
    ]
    assert to_ragas_rows(rows) == [
        {"user_input": "q1", "response": "a1", "retrieved_contexts": ["c"], "reference": "g1"}]


# ---------------- the CLI (sync: it calls asyncio.run itself) ----------------
def test_cli_fast_mode_writes_a_report_and_gates_control_the_exit_code(tmp_path):
    out = tmp_path / "r.json"
    assert main(["--fast", "--out", str(out), "--min", "hit_at_3=0.5"]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["config"]["mode"] == "fast" and report["retrieval"]["n_answerable"] == 28
    assert report["gates"] == {"hit_at_3": 0.5} and report["failures"] == []

    assert main(["--fast", "--out", str(out), "--min", "hit_at_3=0.999"]) == 1          # below the bar
    assert main(["--fast", "--out", str(out), "--min", "faithfulness=0.5"]) == 1        # not measured => fail
    assert json.loads(out.read_text(encoding="utf-8"))["failures"]


def test_cli_ragas_requires_the_real_embedder_and_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "groq_api_key", "")
    with pytest.raises(SystemExit, match="GROQ_API_KEY"):
        main(["--fast", "--generate", "--out", str(tmp_path / "x.json")])
