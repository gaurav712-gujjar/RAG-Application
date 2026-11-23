def make_embedder(settings):
    """Pick the embedder from EMBEDDING_PROVIDER: "gemini" (hosted), "fastembed" (ONNX) or "local" (torch)."""
    provider = settings.embedding_provider

    if provider == "gemini":
        from app.embeddings.gemini import GeminiEmbedder

        return GeminiEmbedder(settings.gemini_api_key, settings.embedding_model, settings.embedding_dim)

    if provider == "fastembed":
        from app.embeddings.fast import FastEmbedEmbedder

        return FastEmbedEmbedder(settings.embedding_model, settings.embedding_dim)

    if provider == "local":
        from app.embeddings.embedder import SentenceTransformerEmbedder

        return SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_dim)

    raise ValueError(f"Unknown EMBEDDING_PROVIDER={provider!r}. Use 'gemini', 'fastembed' or 'local'.")