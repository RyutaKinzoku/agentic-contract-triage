"""Text embeddings for the knowledge base.

Defines an :class:`Embeddings` interface and a Gemini-backed implementation.
Indexing and querying deliberately use different task types
(RETRIEVAL_DOCUMENT vs RETRIEVAL_QUERY); the Gemini embedding model produces
better retrieval results when told how each text will be used.

Keeping embeddings behind an interface lets the knowledge base depend on the
abstraction and lets tests substitute a deterministic stub.
"""

from typing import Protocol, runtime_checkable

from google import genai
from google.genai import types

# 768 is one of the recommended output sizes and keeps vectors small.
DEFAULT_EMBEDDING_DIM = 768
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"


@runtime_checkable
class Embeddings(Protocol):
    """Turns text into vectors for indexing and querying."""

    @property
    def dimension(self) -> int:
        """The length of the vectors this embedder produces."""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed texts that will be stored in the index.

        Args:
            texts: The documents to embed.

        Returns:
            One vector per input text, in the same order.
        """
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query.

        Args:
            text: The query text.

        Returns:
            The query vector.
        """
        ...


class GeminiEmbeddings:
    """Embeddings backed by the Gemini embedding model.

    Args:
        api_key: Gemini API key.
        model: Embedding model identifier.
        dimension: Output vector size (truncated from the model's default).
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimension: int = DEFAULT_EMBEDDING_DIM,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        """See :class:`Embeddings`."""
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """See :class:`Embeddings`. Uses the RETRIEVAL_DOCUMENT task type."""
        return self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        """See :class:`Embeddings`. Uses the RETRIEVAL_QUERY task type."""
        return self._embed([text], task_type="RETRIEVAL_QUERY")[0]

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        """Call the Gemini embedding API.

        Args:
            texts: Texts to embed.
            task_type: Either RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY.

        Returns:
            One vector per input text.
        """
        response = self._client.models.embed_content(
            model=self._model,
            contents=texts,
            config=types.EmbedContentConfig(
                output_dimensionality=self._dimension,
                task_type=task_type,
            ),
        )
        return [list(embedding.values) for embedding in response.embeddings]