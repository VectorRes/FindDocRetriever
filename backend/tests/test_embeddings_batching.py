"""OpenAI embeddings are requested in batches within the API's per-request
limits (300,000 tokens, 2,048 inputs), so a large PDF can be ingested. No
real API calls: the client is replaced by a fake that enforces the limits."""
from types import SimpleNamespace

import pytest

from app.embeddings.openai_provider import (
    MAX_INPUTS_PER_REQUEST, MAX_TOKENS_PER_REQUEST, OpenAIEmbeddingProvider, batches,
)


def _words(text):
    return len(text.split())


def test_batches_respect_the_token_budget_and_keep_order():
    texts = [f"t{i} " * 40 for i in range(10)]  # 40 "tokens" each
    groups = list(batches(texts, _words, max_tokens=100, max_inputs=50))
    assert [len(g) for g in groups] == [2, 2, 2, 2, 2]
    assert [t for g in groups for t in g] == texts


def test_batches_respect_the_input_limit():
    groups = list(batches(["a"] * 7, _words, max_tokens=1_000, max_inputs=3))
    assert [len(g) for g in groups] == [3, 3, 1]


def test_a_text_over_the_budget_goes_alone():
    groups = list(batches(["a", "b " * 500, "c"], _words, max_tokens=100, max_inputs=10))
    assert [len(g) for g in groups] == [1, 1, 1]


def test_empty_input_makes_no_request():
    assert list(batches([], _words)) == []


class _FakeEmbeddings:
    """Rejects a request over OpenAI's limits, like the real API; returns the
    items out of order to check they're put back by index."""

    def __init__(self, count_tokens):
        self.count_tokens = count_tokens
        self.requests = []

    def create(self, model, input):
        tokens = sum(self.count_tokens(text) for text in input)
        if tokens > 300_000 or len(input) > 2_048:
            raise AssertionError(f"request over the limits: {tokens} tokens, {len(input)} inputs")
        self.requests.append(len(input))
        data = [SimpleNamespace(index=i, embedding=[float(text.split()[0])]) for i, text in enumerate(input)]
        return SimpleNamespace(data=list(reversed(data)))


def test_a_large_pdf_is_embedded_in_several_requests_in_order():
    provider = OpenAIEmbeddingProvider(api_key="test", model="text-embedding-3-small", dimensions=1)
    provider._count_tokens = _words
    fake = _FakeEmbeddings(_words)
    provider._client = SimpleNamespace(embeddings=fake)
    # ~464,000 tokens over 5,000 chunks, as in the PDF that failed.
    texts = [f"{i} " + "word " * 92 for i in range(5_000)]

    embeddings = provider.embed(texts)

    assert len(fake.requests) > 1
    assert all(n <= MAX_INPUTS_PER_REQUEST for n in fake.requests)
    assert [e[0] for e in embeddings] == [float(i) for i in range(5_000)]


class _StricterEmbeddings(_FakeEmbeddings):
    """Counts 30% more tokens than our counter (as OpenAI did: 310,672 for a
    batch counted under 250,000) and rejects like the real API."""

    def create(self, model, input):
        import httpx
        from openai import BadRequestError

        tokens = int(sum(self.count_tokens(text) for text in input) * 1.3)
        if tokens > 300_000:
            self.rejected = getattr(self, "rejected", 0) + 1
            message = f"Requested {tokens} tokens, max 300000 tokens per request"
            error = {"message": message, "type": "max_tokens_per_request", "code": "max_tokens_per_request"}
            response = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/embeddings"),
                                      json={"error": error})
            raise BadRequestError(f"Error code: 400 - {error}", response=response, body=error)
        return super().create(model, input)


def test_a_batch_rejected_as_too_large_is_split_and_retried():
    provider = OpenAIEmbeddingProvider(api_key="test", model="text-embedding-3-small", dimensions=1)
    provider._count_tokens = _words
    fake = _StricterEmbeddings(_words)
    provider._client = SimpleNamespace(embeddings=fake)
    texts = [f"{i} " + "word " * 199 for i in range(1_400)]  # 280,000 by our count, 364,000 by the API's

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.embeddings.openai_provider.MAX_TOKENS_PER_REQUEST", 290_000)
        embeddings = provider.embed(texts)

    assert fake.rejected == 1  # the one batch was too large for the API...
    assert len(fake.requests) == 2  # ...so it went again as two halves
    assert [e[0] for e in embeddings] == [float(i) for i in range(1_400)]


def test_other_errors_are_not_retried():
    from openai import BadRequestError
    import httpx

    class _Broken:
        calls = 0

        def create(self, model, input):
            _Broken.calls += 1
            response = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/embeddings"))
            raise BadRequestError("invalid input", response=response, body={"code": "invalid_input"})

    provider = OpenAIEmbeddingProvider(api_key="test", model="text-embedding-3-small", dimensions=1)
    provider._count_tokens = _words
    provider._client = SimpleNamespace(embeddings=_Broken())
    with pytest.raises(BadRequestError):
        provider.embed(["a", "b", "c"])
    assert _Broken.calls == 1


def test_limits_stay_below_openais():
    assert MAX_TOKENS_PER_REQUEST < 300_000 and MAX_INPUTS_PER_REQUEST <= 2_048


@pytest.mark.parametrize("text", ["Ingresos operacionales: 2024=4,820,000", ""])
def test_token_counter_counts_something(text):
    from app.embeddings.openai_provider import _token_counter

    assert _token_counter()(text) >= 0
