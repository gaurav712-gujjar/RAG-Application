def rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion. Each ranking is a list of ids, best first.
    score(id) = sum over rankings of 1 / (k + rank). Rank-based, so dense cosine scores and BM25
    scores never need to be put on the same scale."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores
