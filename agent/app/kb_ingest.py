"""Loading and ingestion logic for the knowledge base.

Separated from the CLI script (`scripts/ingest_kb.py`) so the record-building
and ingestion steps can be unit tested with a stub embedder and an in-memory
store, without reading the real files or calling the network.

Point ids are derived deterministically with uuid5, so re-running ingestion
updates existing points instead of creating duplicates (idempotent).
"""

import json
import uuid
from pathlib import Path
from typing import Any

import yaml

from app.embeddings import Embeddings
from app.vectorstore import Record, VectorStore

# Fixed namespace so the same source item always maps to the same point id.
_NAMESPACE = uuid.UUID("6f9b9f3e-1c2d-4f3a-9b8c-2a1e7d4c5b6a")

KB_DIR = Path(__file__).resolve().parent.parent / "knowledge_base"
POLICIES_FILE = KB_DIR / "policies.yaml"
CLIENTS_FILE = KB_DIR / "known_clients.json"


def load_policies(path: Path = POLICIES_FILE) -> list[dict[str, Any]]:
    """Load policy rules from a YAML file.

    Args:
        path: Path to the policies YAML file.

    Returns:
        A list of policy dicts with 'id', 'category', and 'statement'.
    """
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_clients(path: Path = CLIENTS_FILE) -> list[dict[str, Any]]:
    """Load known clients from a JSON file.

    Args:
        path: Path to the clients JSON file.

    Returns:
        A list of client dicts, each with at least a 'name'.
    """
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)["clients"]


def build_records(
    embeddings: Embeddings,
    policies: list[dict[str, Any]],
    clients: list[dict[str, Any]],
) -> list[Record]:
    """Embed policies and clients into storable records.

    Policy statements and client names are embedded as documents. Each record's
    payload carries a 'type' ('policy' or 'client') used for filtered search.

    Args:
        embeddings: The embedder to use.
        policies: Policy dicts from :func:`load_policies`.
        clients: Client dicts from :func:`load_clients`.

    Returns:
        Records ready to upsert into a vector store.
    """
    policy_texts = [policy["statement"].strip() for policy in policies]
    client_names = [client["name"] for client in clients]

    policy_vectors = embeddings.embed_documents(policy_texts) if policy_texts else []
    client_vectors = embeddings.embed_documents(client_names) if client_names else []

    records: list[Record] = []
    for policy, vector in zip(policies, policy_vectors):
        records.append(
            Record(
                id=str(uuid.uuid5(_NAMESPACE, f"policy:{policy['id']}")),
                vector=vector,
                payload={
                    "type": "policy",
                    "policy_id": policy["id"],
                    "category": policy["category"],
                    "statement": policy["statement"].strip(),
                },
            )
        )
    for client, vector in zip(clients, client_vectors):
        records.append(
            Record(
                id=str(uuid.uuid5(_NAMESPACE, f"client:{client['name']}")),
                vector=vector,
                payload={"type": "client", **client},
            )
        )
    return records


def ingest(
    embeddings: Embeddings,
    store: VectorStore,
    policies: list[dict[str, Any]],
    clients: list[dict[str, Any]],
) -> int:
    """Embed and upsert the knowledge base into the store.

    Args:
        embeddings: The embedder to use.
        store: The destination vector store.
        policies: Policy dicts.
        clients: Client dicts.

    Returns:
        The number of records ingested.
    """
    store.ensure_collection(embeddings.dimension)
    records = build_records(embeddings, policies, clients)
    if records:
        store.upsert(records)
    return len(records)