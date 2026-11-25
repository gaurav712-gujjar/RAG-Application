"""Does an embedding model find the right passage for Hinglish / Hindi questions?

Run it yourself (needs your key and internet):
    set GEMINI_API_KEY=...        (PowerShell: $env:GEMINI_API_KEY="...")
    python -m evals.hinglish_embed_check
    python -m evals.hinglish_embed_check --dim 1536       # try a larger vector size
    python -m evals.hinglish_embed_check --fastembed      # also compare the local MiniLM model

It prints, for every question, which passage ranked first and whether that was right, then the
overall accuracy and mean reciprocal rank (MRR). 1.0 is perfect.
"""
import argparse
import asyncio
import math
import os

PASSAGES = {
    "refund_en": "Refunds are processed within 14 days of delivery. Items must be unused and in original packaging.",
    "refund_hi": "रिफंड डिलीवरी के 14 दिनों के भीतर किया जाता है। सामान इस्तेमाल न किया हुआ और मूल पैकेजिंग में होना चाहिए।",
    "warranty_en": "The device carries a 1 year warranty. Water damage is not covered by the warranty.",
    "warranty_hi": "डिवाइस पर 1 साल की वारंटी है। पानी से हुए नुकसान पर वारंटी लागू नहीं होती।",
    "shipping_en": "Standard shipping takes 5 to 7 business days. Express shipping is delivered in 2 days.",
    "shipping_mix": "Standard delivery me 5 se 7 working days lagte hain. Express delivery 2 din me ho jaati hai.",
    "leave_en": "Employees get 18 paid leaves per year. Unused leaves can be carried forward up to 10 days.",
    "router_en": "To reset the router, hold the reset button for 10 seconds until the lights blink.",
}

# (question, id of the passage that should come first)
QUESTIONS = [
    ("refund kitne din me milta hai?", "refund_en"),
    ("paisa wapas kitne din me aata hai", "refund_en"),
    ("रिफंड कितने दिन में मिलता है?", "refund_hi"),
    ("warranty kitne time ki hoti hai?", "warranty_en"),
    ("pani se kharab hua phone warranty me aayega kya?", "warranty_en"),
    ("वारंटी कितने समय की होती है?", "warranty_hi"),
    ("delivery me kitne din lagte hain?", "shipping_mix"),
    ("shipping kitni der me hoti hai", "shipping_en"),
    ("saal me kitni chhutti milti hain?", "leave_en"),
    ("router ko reset kaise karte hain?", "router_en"),
    ("How many days do I have to ask for a refund?", "refund_en"),
]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


async def evaluate(name, embedder):
    ids = list(PASSAGES)
    pvecs = await embedder.embed([PASSAGES[i] for i in ids])
    qvecs = await embedder.embed([q for q, _ in QUESTIONS])
    hits, rr = 0, 0.0
    print(f"\n=== {name} ===")
    for (q, want), qv in zip(QUESTIONS, qvecs):
        ranked = sorted(ids, key=lambda i: cos(qv, pvecs[ids.index(i)]), reverse=True)
        ok = ranked[0] == want
        hits += ok
        rr += 1 / (ranked.index(want) + 1)
        print(f"{'OK  ' if ok else 'MISS'} {q!r:60} -> {ranked[0]}" + ("" if ok else f" (wanted {want}, rank {ranked.index(want) + 1})"))
    n = len(QUESTIONS)
    print(f"accuracy {hits}/{n} = {hits / n:.2f}   MRR {rr / n:.2f}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-embedding-001")
    ap.add_argument("--dim", type=int, default=768)
    ap.add_argument("--fastembed", action="store_true", help="also test the local MiniLM model")
    args = ap.parse_args()

    from app.embeddings.gemini import GeminiEmbedder

    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise SystemExit("Set GEMINI_API_KEY first.")
    await evaluate(f"{args.model} (dim {args.dim})", GeminiEmbedder(key, args.model, args.dim))
    if args.fastembed:
        from app.embeddings.fast import FastEmbedEmbedder

        await evaluate("MiniLM (fastembed)", FastEmbedEmbedder("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384))


if __name__ == "__main__":
    asyncio.run(main())
