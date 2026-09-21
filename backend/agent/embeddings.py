"""bge-small-en-v1.5 via fastembed (ONNX).

fastembed rather than sentence-transformers: it ships the same model as a
~130MB ONNX graph with no torch, which on a single VPS batch is the difference
between a 200MB install and a 2GB one.

Passages and queries are embedded *differently*. bge is an asymmetric model --
queries need the instruction prefix below, passages must not have it. Skipping
the prefix measurably degrades retrieval, so the two are separate functions
rather than one function with a flag a caller can forget.
"""

from functools import lru_cache

from app.config import get_settings

# The instruction bge-*-en-v1.5 was trained with for retrieval queries.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# fastembed streams batches through ONNX; this bounds peak memory on a small VPS.
BATCH_SIZE = 64


@lru_cache(maxsize=1)
def _model():
    # Imported lazily so that merely importing this module (as the test suite
    # and `/health` do) does not pay the model construction cost.
    from fastembed import TextEmbedding

    settings = get_settings()
    settings.fastembed_cache.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(
        model_name=settings.embedding_model,
        cache_dir=str(settings.fastembed_cache),
    )


def warm() -> None:
    """Construct the model ahead of first use.

    Called from the app lifespan: the first call downloads ~130MB, and paying
    that inside a user's upload request makes ingestion look broken.
    """
    _model()


def embed_passages(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return [v.tolist() for v in _model().embed(texts, batch_size=BATCH_SIZE)]


def embed_query(text: str) -> list[float]:
    return next(iter(_model().embed([QUERY_PREFIX + text]))).tolist()
