import logging
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, Response, status, HTTPException, BackgroundTasks
from pydantic import BaseModel

from core.db import db_manager
from core.utils import check_ollama_models
from ingestion import ingest_text, QDRANT_COLLECTION_NAME
from query import run_query_pipeline
from agent import run_agent, clear_session, get_history

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
    version="0.3.0",
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


class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    sources: List[SourceChunk]
    graph_context: str
    reasoning: List[str]
    strategy: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/", tags=["General"])
def read_root():
    return {"message": "Hello from OmniGraph Orchestration Service v0.3.0"}


@app.get(
    "/health",
    tags=["Health"],
    response_model=HealthCheckResponse,
    summary="Deep health check on connected services",
)
async def health_check(response: Response) -> HealthCheckResponse:
    is_healthy = True
    statuses: Dict[str, HealthStatus] = {}

    try:
        await db_manager.neo4j_driver.verify_connectivity()
        statuses["neo4j"] = HealthStatus(status="healthy")
    except Exception as e:
        is_healthy = False
        statuses["neo4j"] = HealthStatus(status="unhealthy")
        logger.error(f"Neo4j health check failed: {e}")

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


@app.post(
    "/collections/clear",
    status_code=status.HTTP_200_OK,
    tags=["Admin"],
    summary="[Admin] Clear the main Qdrant collection",
)
async def clear_main_qdrant_collection():
    """
    Deletes all vectors and metadata from the main `omnigraph_chunks` collection.
    This is a destructive operation.

    The collection will be recreated automatically on the next ingestion.
    """
    client = db_manager.qdrant_client
    if not client:
        raise HTTPException(status_code=503, detail="Qdrant client is not available.")

    collection_name = QDRANT_COLLECTION_NAME
    try:
        logger.warning(f"Received request to delete Qdrant collection: '{collection_name}'")
        result = await client.delete_collection(collection_name=collection_name)

        if result:
            logger.info(f"Successfully deleted Qdrant collection '{collection_name}'.")
            return {"message": f"Collection '{collection_name}' was deleted successfully."}
        else:
            logger.warning(f"Attempted to delete collection '{collection_name}', but it did not exist.")
            return {"message": f"Collection '{collection_name}' did not exist or was already deleted."}

    except Exception as e:
        logger.error(f"Failed to delete Qdrant collection '{collection_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete collection: {str(e)}")


@app.post(
    "/query",
    tags=["Query"],
    response_model=QueryResponse,
    summary="Simple RAG query — vector + graph + LLM (no agent, no history)",
)
async def query_endpoint(request: QueryRequest) -> QueryResponse:
    """Phase 2 endpoint — kept for backwards compatibility."""
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


@app.post(
    "/chat",
    tags=["Agent"],
    response_model=ChatResponse,
    summary="Agentic chat — smart routing, retry logic, and conversation memory",
)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    """
    Phase 3 LangGraph agent.
    Omit session_id to start a new session — the ID is returned in the response.
    Pass the same session_id on follow-up questions for multi-turn memory.
    """
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    try:
        result = await run_agent(
            question=request.question,
            session_id=request.session_id,
        )
        return ChatResponse(
            answer=result["answer"],
            session_id=result["session_id"],
            sources=[SourceChunk(**s) for s in result["sources"]],
            graph_context=result["graph_context"],
            reasoning=result["reasoning"],
            strategy=result["strategy"],
        )
    except Exception as e:
        logger.error(f"Agent failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")


@app.delete(
    "/chat/{session_id}",
    tags=["Agent"],
    summary="Clear a chat session's conversation history",
)
async def clear_chat_session(session_id: str):
    clear_session(session_id)
    return {"message": f"Session '{session_id}' cleared."}


@app.get(
    "/chat/{session_id}/history",
    tags=["Agent"],
    summary="Get the conversation history for a session",
)
async def get_chat_history(session_id: str):
    history = get_history(session_id)
    if not history:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return {"session_id": session_id, "history": history}