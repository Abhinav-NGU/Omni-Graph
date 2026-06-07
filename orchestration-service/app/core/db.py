import logging
from typing import Optional

from neo4j import AsyncGraphDatabase, AsyncDriver
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from tenacity import retry, stop_after_attempt, wait_fixed

from .config import settings

logger = logging.getLogger(__name__)

class DatabaseManager:
    """
    Manages connections to Neo4j and Qdrant databases.
    Includes retry logic for initial connections and graceful shutdown handlers.
    """
    def __init__(self):
        self.neo4j_driver: Optional[AsyncDriver] = None
        self.qdrant_client: Optional[AsyncQdrantClient] = None
        self.redis: Optional[Redis] = None

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(3), reraise=True)
    async def connect_to_neo4j(self):
        """Establishes a connection to the Neo4j database with retry logic."""
        try:
            logger.info("Attempting to connect to Neo4j...")
            self.neo4j_driver = AsyncGraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD.get_secret_value())
            )
            await self.neo4j_driver.verify_connectivity()
            logger.info("Successfully connected to Neo4j.")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j. Retrying... Error: {e}")
            raise

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(3), reraise=True)
    async def connect_to_qdrant(self):
        """Establishes a connection to the Qdrant database with retry logic."""
        try:
            logger.info("Attempting to connect to Qdrant...")
            self.qdrant_client = AsyncQdrantClient(url=settings.QDRANT_URL)
            # Verify connectivity by fetching collections
            await self.qdrant_client.get_collections()
            logger.info("Successfully connected to Qdrant.")
        except Exception as e:
            logger.error(f"Failed to connect to Qdrant. Retrying... Error: {e}")
            raise

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(3), reraise=True)
    async def connect_to_redis(self):
        try:
            logger.info("Attempting to connect to Redis...")
            self.redis = Redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
            await self.redis.ping()
            logger.info("Successfully connected to Redis.")
        except Exception as e:
            logger.error(f"Failed to connect to Redis. Retrying... Error: {e}")
            raise

    async def close_connections(self):
        """Closes all database connections."""
        if self.neo4j_driver:
            await self.neo4j_driver.close()
            logger.info("Neo4j connection closed.")
        if self.qdrant_client:
            await self.qdrant_client.close()
            logger.info("Qdrant connection closed.")
        if self.redis:
            await self.redis.aclose()
            logger.info("Redis connection closed.")

db_manager = DatabaseManager()