"""The knowledge base: retrieval over policies and known clients.

:class:`KnowledgeBase` is a thin facade composing an :class:`Embeddings`
provider and a :class:`VectorStore`. It exposes two domain queries used by the
validation step:

- :meth:`search_policies` — find the policy rules most relevant to a term, so
  the validator can check the term against the right rule.
- :meth:`match_counterparty` — find the closest known client to an extracted
  counterparty name, using vector similarity so aliases and minor variations
  ("Acme Corp" vs "Acme Corporation Ltd") still match.

This is where retrieval turns into something the rest of the system can act on.
"""

from dataclasses import dataclass

from app.embeddings import Embeddings
from app.vectorstore import VectorStore

# Cosine score above which a counterparty is treated as a known client.
DEFAULT_KNOWN_CLIENT_THRESHOLD = 0.82


@dataclass(frozen=True)
class PolicyMatch:
    """A policy rule retrieved as relevant to a contract term.

    Attributes:
        policy_id: Stable identifier of the policy rule.
        category: The term category the rule governs (e.g. 'liability').
        statement: The natural-language rule text.
        score: Similarity to the query.
    """

    policy_id: str
    category: str
    statement: str
    score: float


@dataclass(frozen=True)
class CounterpartyMatch:
    """The closest known client to an extracted counterparty name.

    Attributes:
        name: The matched client's canonical name.
        score: Similarity between the query and the matched client.
        is_known: True if the score clears the known-client threshold.
    """

    name: str
    score: float
    is_known: bool


class KnowledgeBase:
    """Retrieval facade over the policy playbook and known-clients register.

    Args:
        embeddings: Provider used to embed queries.
        store: The vector store holding policy and client records.
        known_client_threshold: Minimum similarity to treat a counterparty as a
            known client.
    """

    def __init__(
        self,
        embeddings: Embeddings,
        store: VectorStore,
        known_client_threshold: float = DEFAULT_KNOWN_CLIENT_THRESHOLD,
    ) -> None:
        self._embeddings = embeddings
        self._store = store
        self._threshold = known_client_threshold

    def search_policies(self, query: str, limit: int = 3) -> list[PolicyMatch]:
        """Retrieve the policy rules most relevant to a query.

        Args:
            query: A description of the term to check (e.g. 'liability cap is
                unlimited').
            limit: Maximum number of policies to return.

        Returns:
            Matching policies ordered from most to least relevant.
        """
        vector = self._embeddings.embed_query(query)
        hits = self._store.search(vector, limit=limit, type_filter="policy")
        return [
            PolicyMatch(
                policy_id=hit.payload["policy_id"],
                category=hit.payload["category"],
                statement=hit.payload["statement"],
                score=hit.score,
            )
            for hit in hits
        ]

    def match_counterparty(self, name: str) -> CounterpartyMatch | None:
        """Find the closest known client to a counterparty name.

        Args:
            name: The extracted counterparty name.

        Returns:
            The closest match, or None if the name is empty or no clients are
            indexed.
        """
        if not name or not name.strip():
            return None

        vector = self._embeddings.embed_query(name)
        hits = self._store.search(vector, limit=1, type_filter="client")
        if not hits:
            return None

        top = hits[0]
        return CounterpartyMatch(
            name=top.payload["name"],
            score=top.score,
            is_known=top.score >= self._threshold,
        )