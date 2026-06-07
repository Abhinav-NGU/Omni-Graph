import logging
import httpx
from .config import settings

logger = logging.getLogger(__name__)

async def check_ollama_models():
    """
    Checks if the required Ollama models are available and logs a warning if not.
    """
    required_models = {"nomic-embed-text", "llama3"}
    logger.info("Checking for required Ollama models...")

    try:
        async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL, timeout=20.0) as client:
            response = await client.get("/api/tags")
            response.raise_for_status()
            installed_models_data = response.json().get("models", [])
            installed_models = {model['name'].split(':')[0] for model in installed_models_data}

            missing_models = required_models - installed_models
            if missing_models:
                logger.warning(f"Ollama is missing required models: {', '.join(missing_models)}")
                logger.warning("This will cause ingestion to fail. Please pull the models by running:")
                for model in missing_models:
                    logger.warning(f"  docker exec -it ollama ollama pull {model}")
            else:
                logger.info("All required Ollama models are installed.")
    except Exception as e:
        logger.error(f"Could not connect to or query Ollama to check for models. Please ensure it is running. Error: {e}")