"""Evaluate the RAG pipeline against the golden set.

    python -m evals.run_eval --fast                       # seconds, no downloads (pipeline plumbing check)
    python -m evals.run_eval                              # real models: bge-m3 (+ reranker) retrieval quality
    python -m evals.run_eval --generate                   # + answers from Groq (needs GROQ_API_KEY)
    python -m evals.run_eval --generate --ragas           # + RAGAS judge scores (token-hungry)
    python -m evals.run_eval --min hit_at_3=0.9 --min mrr=0.7   # exit code 1 if a gate fails (CI)

`--fast` uses a hashed bag-of-words embedder: it checks that everything is wired correctly but says
nothing about real quality, and its abstain numbers are meaningless. Use the real mode to tune
RELEVANCE_THRESHOLD / RERANK_THRESHOLD and to compare changes (chunk size, models, prompts).
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

from app.core.config import get_settings
from evals.dataset import load_qa
from evals.metrics import check_thresholds, summarize_generation, summarize_retrieval
from evals.pipeline import build_index, eval_settings, run_generation, run_retrieval

RESULTS_DIR = Path(__file__).parent / "results"


def parse_minimums(pairs: list[str]) -> dict[str, float]:
    out = {}
    for p in pairs:
        name, _, value = p.partition("=")
        if not name or not value:
            raise SystemExit(f"--min expects name=value, got {p!r}")
        out[name.strip()] = float(value)
    return out


async def run(args) -> dict:
    qa = load_qa()
    if args.fast:
        from app.embeddings.hashing import HashingEmbedder

        embedder, reranker = HashingEmbedder(), None
        settings = eval_settings(relevance_threshold=0.05)  # hashed cosines are tiny; see module docstring
    else:
        from app.embeddings.embedder import SentenceTransformerEmbedder
        from app.retrieval.rerank import CrossEncoderReranker

        base = get_settings()
        embedder = SentenceTransformerEmbedder(base.embedding_model, base.embedding_dim)
        await embedder.warmup()
        reranker = None
        if base.rerank_enabled and not args.no_rerank:
            reranker = CrossEncoderReranker(base.reranker_model)
            await reranker.warmup()
        settings = eval_settings()

    index = await build_index(embedder, reranker, settings)
    results, hits_by_id = await run_retrieval(index, qa)
    report = {
        "config": {
            "mode": "fast" if args.fast else "real", "chunks": index.n_chunks, "hybrid": settings.hybrid_enabled,
            "reranker": settings.reranker_model if reranker else None,
            "embedding_model": "hashing" if args.fast else settings.embedding_model,
            "chunk_size_chars": settings.chunk_size_chars, "relevance_threshold": settings.relevance_threshold,
            "rerank_threshold": settings.rerank_threshold, "top_k": settings.retrieve_top_k,
            "context_top_n": settings.context_top_n,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        },
        "retrieval": summarize_retrieval(results, k=args.k),
    }

    if args.generate or args.ragas:
        if not os.getenv("GROQ_API_KEY") and not settings.groq_api_key:
            raise SystemExit("--generate/--ragas need GROQ_API_KEY")
        from app.llm.groq_client import GroqLLM

        llm = GroqLLM(settings.groq_api_key, max_retries=settings.llm_max_retries, timeout_s=settings.llm_timeout_s)
        gen_results, rows = await run_generation(index, llm, qa, hits_by_id, delay_s=args.delay)
        report["generation"] = summarize_generation(gen_results)
        report["answers"] = [{"id": g.qa_id, "answer": g.answer} for g in gen_results]

        if args.ragas:
            if args.fast:
                raise SystemExit("--ragas needs the real embedder (drop --fast)")
            from evals.ragas_judge import run_ragas
            from langchain_groq import ChatGroq

            judge = ChatGroq(model=args.judge_model, temperature=0, api_key=settings.groq_api_key)
            report["ragas"] = await asyncio.to_thread(
                run_ragas, rows, llm=judge, embed_fn=embedder._encode, max_workers=args.ragas_workers
            )
            report["config"]["judge_model"] = args.judge_model

    report["gates"] = parse_minimums(args.min)
    report["failures"] = check_thresholds(report, report["gates"])
    return report


def print_report(report: dict) -> None:
    c = report["config"]
    print(f"\nmode={c['mode']}  chunks={c['chunks']}  embeddings={c['embedding_model']}  "
          f"reranker={c['reranker'] or 'off'}  hybrid={c['hybrid']}")
    for section in ("retrieval", "generation", "ragas"):
        if section not in report:
            continue
        print(f"\n[{section}]")
        for key, value in report[section].items():
            if key == "by_tag":
                for tag, sub in value.items():
                    print(f"  {'by_tag.' + tag:<34} {sub}")
            elif key == "misses":
                print(f"  {key:<34} {', '.join(value) or '-'}")
            else:
                print(f"  {key:<34} {value}")
    if report["failures"]:
        print("\nFAILED GATES:")
        for f in report["failures"]:
            print("  -", f)
    elif report["gates"]:
        print(f"\nall {len(report['gates'])} gates passed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true", help="hashing embedder, no model downloads (plumbing check only)")
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--generate", action="store_true", help="also generate answers with Groq")
    ap.add_argument("--ragas", action="store_true", help="also run RAGAS judge metrics (implies --generate)")
    ap.add_argument("--judge-model", default="llama-3.3-70b-versatile")
    ap.add_argument("--ragas-workers", type=int, default=2, help="parallel judge calls (keep low for Groq limits)")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between generation calls")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--min", action="append", default=[], metavar="METRIC=VALUE", help="fail (exit 1) below this value")
    ap.add_argument("--out", type=Path, default=None, help="where to write the JSON report")
    args = ap.parse_args(argv)

    report = asyncio.run(run(args))
    print_report(report)

    RESULTS_DIR.mkdir(exist_ok=True)
    out = args.out or RESULTS_DIR / f"{report['config']['mode']}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nreport saved to {out}")
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
