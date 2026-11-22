def make_embedder(settings):
    """Pick the embedder from EMBEDDING_PROVIDER: "fastembed" (ONNX, light) or "local" (torch)."""
    provider = settings.embedding_provider

    if provider == "fastembed":
        from app.embeddings.fast import FastEmbedEmbedder

        return FastEmbedEmbedder(settings.embedding_model, settings.embedding_dim)

    if provider == "local":
        from app.embeddings.embedder import SentenceTransformerEmbedder

        return SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_dim)

    raise ValueError(f"Unknown EMBEDDING_PROVIDER={provider!r}. Use 'fastembed' or 'local'.")