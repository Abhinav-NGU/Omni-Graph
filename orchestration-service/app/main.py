import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status, HTTPException, BackgroundTasks
from pydantic import BaseModel

from core.db import db_manager
from ingestion import ingest_text
from core.utils import check_ollama_models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Orchestration Service...")
    await check_ollama_models()
    try:
        await db_manager.connect_to_neo4j()   # These must be async in db_manager
        await db_manager.connect_to_qdrant()
        yield
    finally:
        logger.info("Shutting down Orchestration Service...")
        await db_manager.close_connections()


app = FastAPI(
    title="OmniGraph Orchestration Service",
    description="This service orchestrates agentic workflows and manages the knowledge graph.",
    version="0.1.0",
    lifespan=lifespan,
    root_path="/orchestration",
)


class HealthStatus(BaseModel):
    status: str


class HealthCheckResponse(BaseModel):
    neo4j: HealthStatus
    qdrant: HealthStatus


@app.get(
    "/health",
    tags=["Health"],
    response_model=HealthCheckResponse,
    summary="Perform deep health checks on connected services",
)
async def health_check(response: Response) -> HealthCheckResponse:
    is_healthy = True
    statuses = {}

    try:
        await db_manager.neo4j_driver.verify_connectivity()
        statuses["neo4j"] = HealthStatus(status="healthy")
    except Exception as e:
        is_healthy = False
        statuses["neo4j"] = HealthStatus(status="unhealthy")
        logger.error(f"Neo4j health check failed: {e}")

    try:
        # The async client doesn't have a health_check method.
        # We can probe its readiness by checking the root path.
        await db_manager.qdrant_client.get_collections()
        statuses["qdrant"] = HealthStatus(status="healthy")
    except Exception as e:
        is_healthy = False
        statuses["qdrant"] = HealthStatus(status="unhealthy")
        logger.error(f"Qdrant health check failed: {e}")

    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthCheckResponse(**statuses)


class IngestRequest(BaseModel):
    text: str


@app.post("/ingest", status_code=status.HTTP_202_ACCEPTED, tags=["Ingestion"])
async def ingest_endpoint(request: IngestRequest, background_tasks: BackgroundTasks):
    """
    Accepts raw text and queues it for background processing through the ingestion pipeline.
    This endpoint is non-blocking and will return immediately.
    """
    try:
        background_tasks.add_task(ingest_text, text=request.text)
        return {"message": "Ingestion has been queued and is processing in the background."}
    except Exception as e:
        logger.error(f"Failed to queue ingestion task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to queue the ingestion task.")


@app.get("/")
def read_root():
    return {"message": "Hello from OmniGraph Orchestration Service"}