"""Pure metric functions (no models, no network) so they are trivially testable."""
from dataclasses import dataclass, field


@dataclass
class RetrievalResult:
    qa_id: str
    tag: str
    answerable: bool
    retrieved_sources: list[str]      # filename of each kept chunk, best first
    retrieved_texts: list[str]
    source: str | None = None         # expected file
    evidence: str | None = None       # text that must appear in a retrieved chunk


def evidence_rank(r: RetrievalResult) -> int | None:
    """1-based rank of the first retrieved chunk that contains the evidence text, else None.
    Checking the text (not just the filename) catches chunking/retrieval that finds the right file
    but the wrong passage."""
    if not r.answerable or not r.evidence:
        return None
    for i, text in enumerate(r.retrieved_texts, start=1):
        if r.evidence in text:
            return i
    return None


def _rate(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def summarize_retrieval(results: list[RetrievalResult], k: int = 3) -> dict:
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    ranks = [evidence_rank(r) for r in answerable]

    report = {
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "hit_at_1": _rate([rk == 1 for rk in ranks]),
        f"hit_at_{k}": _rate([rk is not None and rk <= k for rk in ranks]),
        "mrr": round(sum(1 / rk for rk in ranks if rk) / len(ranks), 4) if ranks else None,
        # Abstain = nothing passed the relevance threshold, so the LLM call is skipped entirely.
        "abstain_rate_unanswerable": _rate([not r.retrieved_texts for r in unanswerable]),
        "false_abstain_rate_answerable": _rate([not r.retrieved_texts for r in answerable]),
        "by_tag": {},
    }
    for tag in sorted({r.tag for r in answerable}):
        subset = [evidence_rank(r) for r in answerable if r.tag == tag]
        report["by_tag"][tag] = {
            "n": len(subset),
            f"hit_at_{k}": _rate([rk is not None and rk <= k for rk in subset]),
        }
    report["misses"] = [r.qa_id for r, rk in zip(answerable, ranks) if rk is None or rk > k]
    return report


@dataclass
class GenerationResult:
    qa_id: str
    tag: str
    answerable: bool
    answer: str
    abstained: bool                   # answered "Not found in documents."
    key_fact: str | None = None
    cited: bool = False               # contains a [n] citation
    tokens: int = 0
    ttft_ms: float | None = None


def summarize_generation(results: list[GenerationResult]) -> dict:
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    answered = [r for r in answerable if not r.abstained]
    ttfts = sorted(r.ttft_ms for r in results if r.ttft_ms is not None)
    return {
        # does the answer contain the key fact? (cheap proxy; RAGAS gives the judged view)
        "fact_in_answer": _rate([bool(r.key_fact) and r.key_fact.lower() in r.answer.lower() for r in answerable]),
        "false_abstain_rate": _rate([r.abstained for r in answerable]),
        "correct_abstain_rate": _rate([r.abstained for r in unanswerable]),
        "citation_rate": _rate([r.cited for r in answered]),
        "avg_tokens": round(sum(r.tokens for r in results) / len(results), 1) if results else None,
        "ttft_ms_p50": ttfts[len(ttfts) // 2] if ttfts else None,
        "ttft_ms_p95": ttfts[min(len(ttfts) - 1, int(len(ttfts) * 0.95))] if ttfts else None,
    }


def check_thresholds(report: dict, minimums: dict[str, float]) -> list[str]:
    """Return a human-readable list of failed gates (empty list = pass).
    A gate on a metric that was not measured is a failure, not a silent pass."""
    failures = []
    for name, minimum in minimums.items():
        value = _lookup(report, name)
        if value is None:
            failures.append(f"{name}: not measured (needed >= {minimum})")
        elif value < minimum:
            failures.append(f"{name}: {value} < {minimum}")
    return failures


def _lookup(report: dict, name: str):
    for section in ("retrieval", "generation", "ragas"):
        if name in report.get(section, {}):
            return report[section][name]
    return None
