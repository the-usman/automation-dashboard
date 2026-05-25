import asyncio
import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


async def connect_to_mongo() -> None:
    global _client
    uri = os.getenv("MONGODB_URI", "")
    if not uri:
        raise RuntimeError("MONGODB_URI environment variable is not set")

    _client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)

    # Best-effort ping — log a warning but don't crash startup if unreachable
    try:
        await asyncio.wait_for(_client.admin.command("ping"), timeout=6)
        logger.info("MongoDB connected successfully")
    except Exception as exc:
        logger.warning("MongoDB ping failed at startup (will retry on first request): %s", exc)


async def close_mongo_connection() -> None:
    global _client
    if _client:
        _client.close()
        _client = None


def get_db() -> AsyncIOMotorDatabase:
    if _client is None:
        raise RuntimeError("MongoDB client not initialised — ensure lifespan ran connect_to_mongo()")
    db_name = os.getenv("MONGODB_DB_NAME", "project_db")
    return _client[db_name]
