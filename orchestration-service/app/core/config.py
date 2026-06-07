import logging
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    Utilizes pydantic-settings for validation and type-hinting.
    """
    # Neo4j Credentials
    NEO4J_URI: str
    NEO4J_USER: str
    NEO4J_PASSWORD: SecretStr

    # Qdrant Configuration
    QDRANT_URL: str

    # Ollama Configuration
    OLLAMA_BASE_URL: str

    # Redis Configuration
    REDIS_URL: str = "redis://redis:6379"
    
    # Jaeger Agent Configuration (for OpenTelemetry)
    JAEGER_AGENT_HOST: str = "jaeger"
    JAEGER_AGENT_PORT: int = 6831

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
logger.info("Application settings loaded successfully.")