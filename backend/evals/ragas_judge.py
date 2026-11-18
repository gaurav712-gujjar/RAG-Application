"""LLM-as-judge scoring with RAGAS (optional; needs requirements-eval.txt and a Groq key).

RAGAS and LangChain release breaking changes often, so the versions are pinned in
requirements-eval.txt and this module is the only place that touches them.

Cost: every metric makes several judge-LLM calls per question, so a run uses many tokens and can hit
Groq rate limits. That is why it is opt-in (`--ragas`) and the judge defaults to the 70B model, not
the small model that wrote the answers.
"""
import math
from collections.abc import Callable

# friendly name -> substring of the metric name RAGAS reports
_METRICS = {
    "faithfulness": "faithfulness",              # is every claim in the answer supported by the context?
    "answer_relevancy": "relevancy",             # does the answer address the question?
    "context_precision": "context_precision",    # are the useful chunks ranked first?
    "context_recall": "context_recall",          # did retrieval fetch everything needed for the reference answer?
}


def to_ragas_rows(rows: list[dict]) -> list[dict]:
    """Only answerable questions that retrieved something: there is nothing to judge otherwise
    (false abstains are already counted by `false_abstain_rate`)."""
    return [
        {"user_input": r["question"], "response": r["answer"],
         "retrieved_contexts": r["contexts"], "reference": r["ground_truth"]}
        for r in rows if r["answerable"] and r["contexts"]
    ]


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if isinstance(v, (int, float)) and not math.isnan(v)]
    return round(sum(clean) / len(clean), 4) if clean else None


def run_ragas(rows: list[dict], *, llm, embed_fn: Callable[[list[str]], list[list[float]]],
              max_workers: int = 2) -> dict:
    """`llm` is a LangChain chat model (e.g. ChatGroq); `embed_fn` maps texts to vectors."""
    from langchain_core.embeddings import Embeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall, ResponseRelevancy
    from ragas.run_config import RunConfig

    class _Embeddings(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return embed_fn(texts)

        def embed_query(self, text: str) -> list[float]:
            return embed_fn([text])[0]

    samples = to_ragas_rows(rows)
    if not samples:
        return {"n": 0}
    result = evaluate(
        EvaluationDataset.from_list(samples),
        metrics=[Faithfulness(), ResponseRelevancy(), LLMContextPrecisionWithReference(), LLMContextRecall()],
        llm=LangchainLLMWrapper(llm),
        embeddings=LangchainEmbeddingsWrapper(_Embeddings()),
        run_config=RunConfig(max_workers=max_workers, timeout=180, max_retries=6),
        raise_exceptions=False,  # a failed judge call becomes NaN for that cell instead of aborting the run
        show_progress=False,
    )
    scores = list(result.scores)  # one {metric_name: value} dict per sample
    out: dict = {"n": len(samples)}
    for friendly, needle in _METRICS.items():
        column = [s[k] for s in scores for k in s if needle in k]
        out[friendly] = _mean(column)
        out[f"{friendly}_failed"] = sum(1 for v in column if v is None or (isinstance(v, float) and math.isnan(v)))
    return out
