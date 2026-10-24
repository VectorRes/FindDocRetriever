from collections.abc import Callable, Iterator

from openai import BadRequestError, OpenAI

from app.embeddings.base import EmbeddingProvider

# OpenAI's limits per embeddings request: 300,000 tokens across all inputs and
# 2,048 inputs. A large PDF has far more than that, so texts are sent in
# batches. OpenAI counts a request's tokens higher than tiktoken does (a batch
# tiktoken put under 250,000 was rejected as 310,672), so the budget keeps a
# wide margin, and a batch rejected as too large is split and retried anyway.
MAX_TOKENS_PER_REQUEST = 150_000
MAX_INPUTS_PER_REQUEST = 2_048


def _too_large(exc: BadRequestError) -> bool:
    return getattr(exc, "code", None) == "max_tokens_per_request" or "max_tokens_per_request" in str(exc)


def _token_counter() -> Callable[[str], int]:
    """Tokens in a text: exact with tiktoken (installed with langchain-openai),
    else a conservative estimate (figures and short words take ~1 token per
    2-3 characters)."""
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")  # text-embedding-3-*
        return lambda text: len(encoding.encode(text, disallowed_special=()))
    except Exception:  # noqa: BLE001 — not installed, or its data can't be downloaded
        return lambda text: len(text) // 2 + 1


def batches(
    texts: list[str],
    count_tokens: Callable[[str], int],
    max_tokens: int = MAX_TOKENS_PER_REQUEST,
    max_inputs: int = MAX_INPUTS_PER_REQUEST,
) -> Iterator[list[str]]:
    """`texts` in order, split into groups within both limits. A text over the
    token budget on its own still goes alone (the API then reports it)."""
    batch: list[str] = []
    tokens = 0
    for text in texts:
        size = count_tokens(text)
        if batch and (tokens + size > max_tokens or len(batch) >= max_inputs):
            yield batch
            batch, tokens = [], 0
        batch.append(text)
        tokens += size
    if batch:
        yield batch


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str | None, model: str, dimensions: int):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai")
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._dimensions = dimensions
        self._count_tokens: Callable[[str], int] | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._count_tokens is None:
            self._count_tokens = _token_counter()
        embeddings: list[list[float]] = []
        for batch in batches(texts, self._count_tokens, MAX_TOKENS_PER_REQUEST, MAX_INPUTS_PER_REQUEST):
            embeddings.extend(self._embed_batch(batch))
        return embeddings

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(model=self._model, input=batch)
        except BadRequestError as exc:
            if len(batch) < 2 or not _too_large(exc):
                raise
            middle = len(batch) // 2
            return self._embed_batch(batch[:middle]) + self._embed_batch(batch[middle:])
        # The API returns one item per input, tagged with its position.
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    @property
    def dimensions(self) -> int:
        return self._dimensions
