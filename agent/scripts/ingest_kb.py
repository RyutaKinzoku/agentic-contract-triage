"""CLI to load the knowledge base into Qdrant.

Run once, and again whenever the policy playbook or client list changes:

    python -m scripts.ingest_kb

Reads configuration from the environment (see app.config), so GEMINI_API_KEY and
QDRANT_URL must be set.
"""

from qdrant_client import QdrantClient

from app.config import get_settings
from app.embeddings import GeminiEmbeddings
from app.kb_ingest import ingest, load_clients, load_policies
from app.vectorstore import QdrantVectorStore


def main() -> None:
    """Embed the knowledge base files and upsert them into Qdrant."""
    settings = get_settings()

    embeddings = GeminiEmbeddings(api_key=settings.gemini_api_key.get_secret_value())
    client = QdrantClient(url=settings.qdrant_url)
    store = QdrantVectorStore(client, settings.qdrant_collection)

    count = ingest(embeddings, store, load_policies(), load_clients())
    print(f"Ingested {count} records into '{settings.qdrant_collection}'.")


if __name__ == "__main__":
    main()