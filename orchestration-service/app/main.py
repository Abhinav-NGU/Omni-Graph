import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from fastapi import FastAPI, Response, status, HTTPException, BackgroundTasks
from pydantic import BaseModel

from core.db import db_manager
from core.utils import check_ollama_models
from ingestion import ingest_text
from query import run_query_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Orchestration Service...")
    await check_ollama_models()
    try:
        await db_manager.connect_to_neo4j()
        await db_manager.connect_to_qdrant()
        yield
    finally:
        logger.info("Shutting down Orchestration Service...")
        await db_manager.close_connections()


app = FastAPI(
    title="OmniGraph Orchestration Service",
    description="Orchestrates agentic workflows and manages the knowledge graph.",
    version="0.2.0",
    lifespan=lifespan,
    root_path="/orchestration",
)


# ── Pydantic models ────────────────────────────────────────────────────────────

class HealthStatus(BaseModel):
    status: str


class HealthCheckResponse(BaseModel):
    neo4j: HealthStatus
    qdrant: HealthStatus


class IngestRequest(BaseModel):
    text: str


class QueryRequest(BaseModel):
    question: str


class SourceChunk(BaseModel):
    id: str
    text: str
    score: float


class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceChunk]
    graph_context: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/", tags=["General"])
def read_root():
    return {"message": "Hello from OmniGraph Orchestration Service"}


@app.get(
    "/health",
    tags=["Health"],
    response_model=HealthCheckResponse,
    summary="Deep health check on connected services",
)
async def health_check(response: Response) -> HealthCheckResponse:
    is_healthy = True
    statuses: Dict[str, HealthStatus] = {}

    # Neo4j
    try:
        await db_manager.neo4j_driver.verify_connectivity()
        statuses["neo4j"] = HealthStatus(status="healthy")
    except Exception as e:
        is_healthy = False
        statuses["neo4j"] = HealthStatus(status="unhealthy")
        logger.error(f"Neo4j health check failed: {e}")

    # Qdrant — use get_collections(), not the non-existent .rest.get("/")
    try:
        await db_manager.qdrant_client.get_collections()
        statuses["qdrant"] = HealthStatus(status="healthy")
    except Exception as e:
        is_healthy = False
        statuses["qdrant"] = HealthStatus(status="unhealthy")
        logger.error(f"Qdrant health check failed: {e}")

    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthCheckResponse(**statuses)


@app.post(
    "/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Ingestion"],
    summary="Queue raw text for background ingestion into Qdrant + Neo4j",
)
async def ingest_endpoint(request: IngestRequest, background_tasks: BackgroundTasks):
    try:
        background_tasks.add_task(ingest_text, text=request.text)
        return {"message": "Ingestion queued and processing in the background."}
    except Exception as e:
        logger.error(f"Failed to queue ingestion task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to queue the ingestion task.")
    
@app.post("/debug/ingest", tags=["Debug"])
async def debug_ingest(request: IngestRequest):
    """
    Synchronous ingest — runs in foreground so errors are visible.
    Use this to diagnose why /ingest is not writing to Neo4j.
    """
    from ingestion import ingest_text
    try:
        await ingest_text(text=request.text)
        return {"message": "Ingest completed successfully."}
    except Exception as e:
        logger.error(f"Debug ingest failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/query",
    tags=["Query"],
    response_model=QueryResponse,
    summary="Ask a question — answered using vector search + graph context + LLM",
)
async def query_endpoint(request: QueryRequest) -> QueryResponse:
    """
    Full RAG + Graph pipeline:

    1. Embed the question (nomic-embed-text)
    2. Semantic vector search → top relevant chunks (Qdrant)
    3. Knowledge-graph context → entity relationships (Neo4j)
    4. LLM synthesis → grounded answer (llama3)
    """
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    try:
        result = await run_query_pipeline(request.question)
        return QueryResponse(
            answer=result["answer"],
            sources=[SourceChunk(**s) for s in result["sources"]],
            graph_context=result["graph_context"],
        )
    except Exception as e:
        logger.error(f"Query pipeline failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query pipeline error: {str(e)}")
