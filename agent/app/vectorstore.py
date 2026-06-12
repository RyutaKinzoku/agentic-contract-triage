"""Vector store access (Repository pattern).

Abstracts persistence behind a :class:`VectorStore` interface so the rest of the
application depends on that interface rather than on Qdrant directly. This keeps
the store swappable and lets tests run against an in-memory Qdrant instance.

The interface speaks in plain :class:`Record` and :class:`SearchHit` objects so
that no Qdrant-specific types leak past this module.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)


@dataclass(frozen=True)
class Record:
    """A point to store: a stable id, its vector, and arbitrary payload.

    Attributes:
        id: Stable unique id (a UUID string).
        vector: The embedding vector.
        payload: Metadata stored alongside the vector.
    """

    id: str
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True)
class SearchHit:
    """A single search result.

    Attributes:
        id: The stored point id.
        score: Similarity score (cosine; higher is closer).
        payload: The stored metadata.
    """

    id: str | int
    score: float
    payload: dict[str, Any]


@runtime_checkable
class VectorStore(Protocol):
    """Persistence interface for embedding vectors."""

    def ensure_collection(self, dimension: int) -> None:
        """Create the collection if it does not already exist.

        Args:
            dimension: Vector size the collection must accept.
        """
        ...

    def upsert(self, records: list[Record]) -> None:
        """Insert or update points.

        Args:
            records: The records to store.
        """
        ...

    def search(
        self, vector: list[float], limit: int, type_filter: str | None = None
    ) -> list[SearchHit]:
        """Return the nearest stored points to a query vector.

        Args:
            vector: The query vector.
            limit: Maximum number of hits to return.
            type_filter: If set, only match points whose payload 'type' equals
                this value.

        Returns:
            Hits ordered from most to least similar.
        """
        ...


class QdrantVectorStore:
    """A :class:`VectorStore` backed by Qdrant.

    Args:
        client: A configured Qdrant client (server-backed or in-memory).
        collection: The collection name to operate on.
    """

    def __init__(self, client: QdrantClient, collection: str) -> None:
        self._client = client
        self._collection = collection

    def ensure_collection(self, dimension: int) -> None:
        """See :class:`VectorStore`. No-op if the collection exists."""
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )

    def upsert(self, records: list[Record]) -> None:
        """See :class:`VectorStore`."""
        points = [
            PointStruct(id=record.id, vector=record.vector, payload=record.payload)
            for record in records
        ]
        self._client.upsert(collection_name=self._collection, points=points)

    def search(
        self, vector: list[float], limit: int, type_filter: str | None = None
    ) -> list[SearchHit]:
        """See :class:`VectorStore`."""
        query_filter = None
        if type_filter is not None:
            query_filter = Filter(
                must=[FieldCondition(key="type", match=MatchValue(value=type_filter))]
            )

        response = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )
        return [
            SearchHit(id=point.id, score=point.score, payload=point.payload or {})
            for point in response.points
        ]