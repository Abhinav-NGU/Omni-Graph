import logging
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, Response, status, HTTPException, BackgroundTasks, File, UploadFile, Depends
from pydantic import BaseModel, Field
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import asyncio
import json

from core.db import db_manager
from core.utils import check_ollama_models
from ingest_loaders import load_pdf, load_url
from ingestion import ingest_text, QDRANT_COLLECTION_NAME
from query import run_query_pipeline
from agent import run_agent, clear_session, get_history

from auth import require_api_key

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Orchestration Service...")
    await check_ollama_models()
    try:
        await db_manager.connect_to_neo4j()
        await db_manager.connect_to_qdrant()
        await db_manager.connect_to_redis()
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

class IngestUrlRequest(BaseModel):
    url: str


class GraphNode(BaseModel):
    id: str
    label: str


class GraphEdge(BaseModel):
    from_node: str = Field(..., alias="from")
    to_node: str = Field(..., alias="to")
    label: str


class GraphVisual(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]

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
    dependencies=[Depends(require_api_key)],
)
async def ingest_endpoint(request: IngestRequest, background_tasks: BackgroundTasks):
    try:
        background_tasks.add_task(ingest_text, text=request.text)
        return {"message": "Ingestion queued and processing in the background."}
    except Exception as e:
        logger.error(f"Failed to queue ingestion task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to queue the ingestion task.")

@app.post(
    "/ingest/pdf",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Ingestion"],
    dependencies=[Depends(require_api_key)],
    summary="Upload a PDF file for background ingestion",
)
async def ingest_pdf_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only .pdf files are supported."
        )
    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        text = await load_pdf(file_bytes)
        background_tasks.add_task(ingest_text, text=text)
        return {
            "message": f"PDF '{file.filename}' extracted and queued for ingestion.",
            "characters_extracted": len(text),
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"PDF ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"PDF processing failed: {str(e)}")

@app.post(
    "/ingest/url",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Ingestion"],
    dependencies=[Depends(require_api_key)],
    summary="Fetch a URL and queue its content for ingestion",
)
async def ingest_url_endpoint(
    request: IngestUrlRequest,
    background_tasks: BackgroundTasks,
):
    if not request.url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="URL must start with http:// or https://"
        )
    try:
        text = await load_url(request.url)
        background_tasks.add_task(ingest_text, text=text)
        return {
            "message": f"URL '{request.url}' fetched and queued for ingestion.",
            "characters_extracted": len(text),
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"URL ingestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"URL fetch failed: {str(e)}")

@app.post(
    "/collections/clear",
    status_code=status.HTTP_200_OK,
    tags=["Admin"],
    dependencies=[Depends(require_api_key)],
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
    "/graph/clear",
    status_code=status.HTTP_200_OK,
    tags=["Admin"],
    dependencies=[Depends(require_api_key)],
    summary="[Admin] Clear all nodes and relationships from Neo4j",
)
async def clear_graph():
    """Deletes all entities and relationships from Neo4j. Irreversible."""
    try:
        async def _delete_all(tx):
            await tx.run("MATCH (n) DETACH DELETE n")

        async with db_manager.neo4j_driver.session() as session:
            await session.execute_write(_delete_all)
            logger.warning("Neo4j graph cleared.")
            return {"message": "All nodes and relationships deleted from Neo4j."}
    except Exception as e:
        logger.error(f"Failed to clear Neo4j: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to clear graph: {str(e)}")

@app.get(
    "/graph/visual_search",
    tags=["Graph"],
    response_model=GraphVisual,
    dependencies=[Depends(require_api_key)],
    summary="Search for a node and return its neighborhood for visualization",
)
async def visual_search_endpoint(q: str):
    """
    Searches for a node by name (case-insensitive, partial match) and returns
    its 1-hop neighborhood in a format suitable for graph visualization.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' must not be empty.")
    try:
        from query import search_node_for_visual
        graph_data = await search_node_for_visual(q)
        return graph_data
    except Exception as e:
        logger.error(f"Visual graph search for '{q}' failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Graph search failed: {str(e)}")


@app.post(
    "/query",
    tags=["Query"],
    response_model=QueryResponse,
    dependencies=[Depends(require_api_key)],
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
    "/chat/stream",
    tags=["Agent"],
    summary="Streaming agentic chat — tokens arrive in real time",
    dependencies=[Depends(require_api_key)],
)
async def chat_stream_endpoint(request: ChatRequest):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    async def event_stream():
        logger.info("=== STREAM START ===")
        try:
            from langchain_ollama.chat_models import ChatOllama
            from core.config import settings
            from agent import (
                get_or_create_session, get_history, append_to_history,
                node_route, node_retrieve, node_grade, AgentState,
            )

            # Step 1 — session
            logger.info("STREAM: getting session...")
            sid = get_or_create_session(request.session_id)
            history = await get_history(sid)
            logger.info(f"STREAM: session={sid}, history_len={len(history)}")

            yield f"data: {json.dumps({'type': 'reasoning', 'content': 'Session ready'})}\n\n"

            # Step 2 — build state
            state: AgentState = {
                "question": request.question,
                "session_id": sid,
                "history": history,
                "strategy": "both",
                "chunks": [],
                "graph_ctx": "",
                "context_sufficient": False,
                "retries": 0,
                "answer": "",
                "reasoning": [],
            }

            # Step 3 — route
            logger.info("STREAM: routing...")
            state = await node_route(state)
            logger.info(f"STREAM: strategy={state['strategy']}")
            yield f"data: {json.dumps({'type': 'reasoning', 'content': 'Strategy: ' + state['strategy']})}\n\n"

            # Step 4 — retrieve
            logger.info("STREAM: retrieving...")
            for attempt in range(3):
                logger.info(f"STREAM: retrieve attempt {attempt+1}")
                state = await node_retrieve(state, compress=False)
                logger.info(f"STREAM: got {len(state['chunks'])} chunks")
                state = await node_grade(state)
                logger.info(f"STREAM: sufficient={state['context_sufficient']}")
                if state.get("context_sufficient"):
                    break

            # Step 5 — send reasoning
            for step in state.get("reasoning", []):
                yield f"data: {json.dumps({'type': 'reasoning', 'content': step})}\n\n"

            # Step 6 — send sources
            sources = state.get("chunks", [])
            safe_sources = [
                {
                    "id": str(c.get("id", "")),
                    "text": str(c.get("text", "")),
                    "score": float(c.get("score", 0)),
                }
                for c in sources
            ]
            logger.info(f"STREAM: sending {len(safe_sources)} sources")
            yield f"data: {json.dumps({'type': 'sources', 'content': safe_sources})}\n\n"

            # Step 7 — send graph
            graph_ctx = state.get("graph_ctx", "")
            if graph_ctx:
                yield f"data: {json.dumps({'type': 'graph', 'content': graph_ctx})}\n\n"

            # Step 8 — build prompt
            chunk_block = (
                "\n\n---\n\n".join(
                    f"[Chunk {i+1} | score {c.get('score', 0):.4f}]\n{c.get('text', '')}"
                    for i, c in enumerate(sources)
                ) if sources else "(no context found)"
            )
            graph_section = (
                f"### Graph Context\n{graph_ctx}" if graph_ctx
                else "### Graph Context\n(none found)"
            )
            history_block = ""
            if history:
                lines = [
                    f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
                    for m in history[-6:]
                ]
                history_block = "\n### Conversation History\n" + "\n".join(lines)

            user_message = (
                f"### Text Chunks\n{chunk_block}\n\n"
                f"{graph_section}"
                f"{history_block}\n\n"
                f"### Question\n{request.question}"
            )
            system_prompt = (
                "You are a knowledgeable assistant. "
                "Answer accurately and concisely using the provided context. "
                "Do not fabricate facts."
            )

            # Step 9 — stream tokens
            logger.info("STREAM: starting LLM stream...")
            llm = ChatOllama(
                model="llama3.2",
                base_url=settings.OLLAMA_BASE_URL,
                temperature=0.2,
            )

            full_answer = ""
            token_count = 0
            async for token in llm.astream([
                ("system", system_prompt),
                ("human", user_message),
            ]):
                text = token.content
                if text:
                    full_answer += text
                    token_count += 1
                    yield f"data: {json.dumps({'type': 'token', 'content': text})}\n\n"

            logger.info(f"STREAM: LLM done, {token_count} tokens, {len(full_answer)} chars")

            # Step 10 — save history
            await append_to_history(sid, "user", request.question)
            await append_to_history(sid, "assistant", full_answer)

            # Step 11 — done
            yield f"data: {json.dumps({'type': 'done', 'session_id': sid, 'strategy': state.get('strategy', 'both')})}\n\n"
            logger.info("=== STREAM END ===")

        except Exception as e:
            logger.error(f"=== STREAM EXCEPTION: {e} ===", exc_info=True)
            try:
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

@app.post(
    "/chat",
    tags=["Agent"],
    response_model=ChatResponse,
    dependencies=[Depends(require_api_key)],
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

@app.get(
    "/chat/sessions",
    tags=["Agent"],
    summary="List all active sessions with metadata",
    dependencies=[Depends(require_api_key)],
)
async def list_sessions():
    """Returns all sessions stored in Redis with topic and preview."""
    try:
        keys = await db_manager.redis.keys("omnigraph:session:*")
        sessions = []
        for key in keys:
            raw = await db_manager.redis.get(key)
            if not raw:
                continue
            history = json.loads(raw)
            if not history:
                continue
            session_id = key.replace("omnigraph:session:", "")
            # First user message = topic
            first_user = next((m for m in history if m["role"] == "user"), None)
            # Last assistant message = preview
            last_assistant = next((m for m in reversed(history) if m["role"] == "assistant"), None)
            topic = first_user["content"] if first_user else "Untitled"
            topic = topic.replace("?", "").strip()
            topic = topic[:40] + "…" if len(topic) > 40 else topic
            preview = last_assistant["content"][:60] + "…" if last_assistant else ""
            sessions.append({
                "id": session_id,
                "topic": topic,
                "preview": preview,
                "message_count": len(history),
            })
        return {"sessions": sessions}
    except Exception as e:
        logger.error(f"Failed to list sessions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete(
    "/chat/{session_id}",
    tags=["Agent"],
    dependencies=[Depends(require_api_key)],
    summary="Clear a chat session's conversation history",
)
async def clear_chat_session(session_id: str):
    await clear_session(session_id)          # ← await
    return {"message": f"Session '{session_id}' cleared."}


@app.get(
    "/chat/{session_id}/history",
    tags=["Agent"],
    dependencies=[Depends(require_api_key)],
    summary="Get the conversation history for a session",
)
async def get_chat_history(session_id: str):
    history = await get_history(session_id)  # ← await
    if not history:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return {"session_id": session_id, "history": history}