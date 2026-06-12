"""Tests for the knowledge base, ingestion, and vector store.

These run the real QdrantVectorStore against an in-memory Qdrant instance, so
the storage and search logic is genuinely exercised. Embeddings are stubbed with
a deterministic function (identical text -> identical vector) so retrieval is
predictable without calling the network.
"""

import hashlib

from qdrant_client import QdrantClient

from app.kb_ingest import build_records, ingest, load_clients, load_policies
from app.knowledge_base import KnowledgeBase
from app.vectorstore import QdrantVectorStore

_DIM = 16


class _StubEmbeddings:
    """Deterministic embedder: same text always maps to the same vector."""

    @property
    def dimension(self) -> int:
        return _DIM

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.strip().encode("utf-8")).digest()
        return [byte / 255.0 for byte in digest[:_DIM]]


def _build_kb() -> KnowledgeBase:
    """Build a KnowledgeBase backed by in-memory Qdrant with the real KB files."""
    embeddings = _StubEmbeddings()
    store = QdrantVectorStore(QdrantClient(":memory:"), collection="kb_test")
    ingest(embeddings, store, load_policies(), load_clients())
    # A near-1.0 threshold means only an (almost) exact-text match counts as
    # "known". Identical strings score ~1.0 under the deterministic stub; the
    # 0.99 floor absorbs float32 rounding in the stored vectors.
    return KnowledgeBase(embeddings, store, known_client_threshold=0.99)


def test_kb_files_load() -> None:
    """The shipped knowledge base files parse and are non-empty."""
    assert len(load_policies()) >= 1
    assert len(load_clients()) >= 1


def test_build_records_tags_types() -> None:
    """Built records are tagged as 'policy' or 'client' and carry vectors."""
    records = build_records(
        _StubEmbeddings(),
        load_policies(),
        load_clients(),
    )
    types = {record.payload["type"] for record in records}

    assert types == {"policy", "client"}
    assert all(len(record.vector) == _DIM for record in records)


def test_search_policies_returns_relevant_rule() -> None:
    """Querying a policy's own statement retrieves that policy first."""
    kb = _build_kb()
    liability = next(p for p in load_policies() if p["id"] == "liability-cap")

    matches = kb.search_policies(liability["statement"], limit=3)

    assert matches
    assert matches[0].policy_id == "liability-cap"
    assert matches[0].category == "liability"


def test_match_counterparty_recognises_known_client() -> None:
    """An exact known-client name is matched and flagged as known."""
    kb = _build_kb()

    match = kb.match_counterparty("Acme Corporation Ltd")

    assert match is not None
    assert match.name == "Acme Corporation Ltd"
    assert match.is_known is True


def test_match_counterparty_flags_unknown_client() -> None:
    """A name that is not in the register is returned but not marked known."""
    kb = _build_kb()

    match = kb.match_counterparty("Wonka Industries Unlimited")

    assert match is not None
    assert match.is_known is False


def test_match_counterparty_handles_empty_name() -> None:
    """An empty counterparty name yields no match rather than an error."""
    kb = _build_kb()

    assert kb.match_counterparty("") is None