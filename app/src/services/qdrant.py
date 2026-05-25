import os

from qdrant_client import AsyncQdrantClient

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        url = os.environ.get("QDRANT_URL", "")
        api_key = os.environ.get("QDRANT_API_KEY", "")
        if not url:
            raise RuntimeError("QDRANT_URL is not set in environment")
        _client = AsyncQdrantClient(url=url, api_key=api_key or None)
    return _client
