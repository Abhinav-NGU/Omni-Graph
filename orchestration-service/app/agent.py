"""
agent.py — Phase 3: LangGraph Agentic Pipeline

Replaces the linear Phase 2 query pipeline with a smart agent that:
  1. Routes the question (vector-only / graph-only / both)
  2. Retrieves from the appropriate sources
  3. Grades the retrieved context — if weak, retries with a broader strategy
  4. Synthesises a final grounded answer
  5. Tracks full conversation history per session (in-memory)
"""

import logging
import uuid
from typing import List, Dict, Any, Optional, Literal
import json

from langchain_ollama.chat_models import ChatOllama
from langchain_ollama.embeddings import OllamaEmbeddings
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from core.config import settings
from core.db import db_manager
from ingestion import QDRANT_COLLECTION_NAME

logger = logging.getLogger(__name__)

TOP_K = 5
MIN_SCORE = 0.35
MAX_GRAPH_RESULTS = 25
MAX_RETRIES = 2


# ── Redis-backed session store ────────────────────────────────────────────────
SESSION_TTL = 60 * 60 * 24  # 24 hours — sessions expire after a day of inactivity
SESSION_PREFIX = "omnigraph:session:"


def _session_key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def get_or_create_session(session_id: Optional[str]) -> str:
    return session_id or str(uuid.uuid4())


async def get_history(session_id: str) -> List[Dict[str, str]]:
    redis = db_manager.redis
    raw = await redis.get(_session_key(session_id))
    if not raw:
        return []
    return json.loads(raw)


async def append_to_history(session_id: str, role: str, content: str):
    redis = db_manager.redis
    key = _session_key(session_id)
    history = await get_history(session_id)
    history.append({"role": role, "content": content})
    await redis.set(key, json.dumps(history), ex=SESSION_TTL)


async def clear_session(session_id: str):
    redis = db_manager.redis
    await redis.delete(_session_key(session_id))


# ── LangGraph state ───────────────────────────────────────────────────────────

class AgentState(TypedDict):
    question: str
    session_id: str
    history: List[Dict[str, str]]
    strategy: Literal["vector", "graph", "both"]
    chunks: List[Dict[str, Any]]
    graph_ctx: str
    context_sufficient: bool
    retries: int
    answer: str
    reasoning: List[str]


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _llm() -> ChatOllama:
    return ChatOllama(
        model="llama3.2",
        base_url=settings.OLLAMA_BASE_URL,
        temperature=0.2,
    )


def _embedder() -> OllamaEmbeddings:
    return OllamaEmbeddings(
        model="nomic-embed-text",
        base_url=settings.OLLAMA_BASE_URL,
    )


# ── Node 1: Route ─────────────────────────────────────────────────────────────

async def node_route(state: AgentState) -> AgentState:
    question = state["question"].lower()
    reasoning = state.get("reasoning", [])

    graph_signals = [
        "how does", "relate", "connection", "connected",
        "relationship", "between", "link", "path", "who works",
        "who founded", "who leads", "part of",
    ]
    vector_signals = [
        "what is", "explain", "describe", "summarise", "summary",
        "tell me about", "definition", "how does it work",
    ]

    is_graph = any(sig in question for sig in graph_signals)
    is_vector = any(sig in question for sig in vector_signals)

    if is_graph and not is_vector:
        strategy = "graph"
        reasoning.append("Routed to: graph-only (relationship question detected)")
    elif is_vector and not is_graph:
        strategy = "vector"
        reasoning.append("Routed to: vector-only (descriptive question detected)")
    else:
        strategy = "both"
        reasoning.append("Routed to: both (complex or ambiguous question)")

    return {**state, "strategy": strategy, "reasoning": reasoning}


# ── Node 2: Retrieve ──────────────────────────────────────────────────────────

async def _vector_search(question: str) -> List[Dict[str, Any]]:
    try:
        query_vector = await _embedder().aembed_query(question)
        client = db_manager.qdrant_client

        collections = await client.get_collections()
        names = [c.name for c in collections.collections]
        if QDRANT_COLLECTION_NAME not in names:
            logger.warning("Qdrant collection not found.")
            return []

        response = await client.query_points(
            collection_name=QDRANT_COLLECTION_NAME,
            query=query_vector,
            limit=TOP_K,
            with_payload=True,
        )
        results = response.points

        chunks = []
        for hit in results:
            text = (hit.payload or {}).get("text", "")
            if text and hit.score >= MIN_SCORE:
                chunks.append({"id": str(hit.id), "text": text, "score": hit.score})

        chunks.sort(key=lambda x: x["score"], reverse=True)
        logger.info(f"Vector search: {len(chunks)} chunks (before filter: {len(results)}).")
        return chunks

    except Exception as e:
        logger.error(f"Vector search failed: {e}", exc_info=True)
        return []
    

async def _graph_search(question: str, broad: bool = False) -> str:
    stop_words = {
        "the", "and", "for", "with", "from", "that", "this",
        "what", "who", "how", "does", "did", "was", "are",
        "is", "in", "of", "to", "a", "an", "me", "about",
    }
    tokens = question.split()
    entity_names = []
    for token in tokens:
        cleaned = token.strip(".,;:\"'?!()")
        if cleaned and len(cleaned) > 2 and cleaned.lower() not in stop_words:
            entity_names.append(cleaned)

    if broad:
        for i in range(len(tokens) - 1):
            phrase = f"{tokens[i].strip('.,;')} {tokens[i+1].strip('.,;')}"
            entity_names.append(phrase)

    entity_names = list(dict.fromkeys(entity_names))[:20]

    if not entity_names:
        return ""

    logger.info(f"Graph search: querying for {entity_names}")

    # Two simple queries — no subquery, no UNWIND+WHERE, works on all Neo4j versions
    one_hop_cypher = """
    UNWIND $names AS name
    MATCH (e:Entity)-[r:RELATES_TO]->(n:Entity)
    WHERE toLower(e.name) CONTAINS toLower(name)
      AND r.type IS NOT NULL
    RETURN e.name AS src, r.type AS rel, n.name AS tgt
    LIMIT $limit
    """

    two_hop_cypher = """
    UNWIND $names AS name
    MATCH (e:Entity)-[r1:RELATES_TO]->(mid:Entity)-[r2:RELATES_TO]->(n:Entity)
    WHERE toLower(e.name) CONTAINS toLower(name)
      AND r1.type IS NOT NULL AND r2.type IS NOT NULL
    RETURN e.name AS src, r1.type AS rel1, mid.name AS mid, r2.type AS rel2, n.name AS tgt
    LIMIT $limit
    """

    try:
        async def _tx(tx):
            lines = []

            # 1-hop
            r1 = await tx.run(one_hop_cypher, names=entity_names, limit=MAX_GRAPH_RESULTS)
            for rec in await r1.data():
                lines.append(f"{rec['src']} --[{rec['rel']}]--> {rec['tgt']}")

            # 2-hop
            r2 = await tx.run(two_hop_cypher, names=entity_names, limit=MAX_GRAPH_RESULTS)
            for rec in await r2.data():
                lines.append(
                    f"{rec['src']} --[{rec['rel1']}]--> {rec['mid']} --[{rec['rel2']}]--> {rec['tgt']}"
                )

            return lines

        async with db_manager.neo4j_driver.session() as session:
            lines = await session.execute_read(_tx)

        # Deduplicate
        lines = list(dict.fromkeys(lines))

        if not lines:
            return ""

        logger.info(f"Graph search: {len(lines)} paths found.")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Graph search failed: {e}", exc_info=True)
        return ""


async def node_retrieve(state: AgentState) -> AgentState:
    strategy = state["strategy"]
    question = state["question"]
    retries = state.get("retries", 0)
    reasoning = state.get("reasoning", [])
    broad = retries > 0

    chunks: List[Dict[str, Any]] = []
    graph_ctx = ""

    if strategy in ("vector", "both"):
        chunks = await _vector_search(question)
        reasoning.append(f"Vector search returned {len(chunks)} chunks.")

    if strategy in ("graph", "both"):
        graph_ctx = await _graph_search(question, broad=broad)
        reasoning.append(f"Graph search returned {'paths' if graph_ctx else 'no paths'}.")

    return {**state, "chunks": chunks, "graph_ctx": graph_ctx, "reasoning": reasoning}


# ── Node 3: Grade ─────────────────────────────────────────────────────────────

async def node_grade(state: AgentState) -> AgentState:
    chunks = state.get("chunks", [])
    graph_ctx = state.get("graph_ctx", "")
    retries = state.get("retries", 0)
    reasoning = state.get("reasoning", [])

    sufficient = bool(chunks) or bool(graph_ctx)

    if not sufficient and retries < MAX_RETRIES:
        reasoning.append(
            f"Context insufficient (retry {retries + 1}/{MAX_RETRIES}). "
            "Escalating to 'both'."
        )
        return {
            **state,
            "context_sufficient": False,
            "strategy": "both",
            "retries": retries + 1,
            "reasoning": reasoning,
        }

    if not sufficient:
        reasoning.append("Context still insufficient after max retries.")
    else:
        reasoning.append(
            f"Context sufficient: {len(chunks)} chunks, "
            f"{'graph paths found' if graph_ctx else 'no graph paths'}."
        )

    return {**state, "context_sufficient": True, "reasoning": reasoning}


def grade_router(state: AgentState) -> str:
    if not state.get("context_sufficient", True):
        return "retrieve"
    return "synthesise"


# ── Node 4: Synthesise ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a knowledgeable assistant with access to:
1. Relevant text chunks retrieved via semantic search.
2. Knowledge-graph paths showing how entities relate to each other.
3. The conversation history with this user.

Use all available context to answer accurately and concisely.
If the context doesn't contain enough information, say so honestly — do not fabricate.
"""


async def node_synthesise(state: AgentState) -> AgentState:
    question = state["question"]
    chunks = state.get("chunks", [])
    graph_ctx = state.get("graph_ctx", "")
    history = state.get("history", [])
    reasoning = state.get("reasoning", [])

    chunk_block = (
        "\n\n---\n\n".join(
            f"[Chunk {i+1} | score {c['score']:.3f}]\n{c['text']}"
            for i, c in enumerate(chunks)
        )
        if chunks else "(no vector context retrieved)"
    )

    graph_section = (
        f"### Graph Paths\n{graph_ctx}"
        if graph_ctx else "### Graph Paths\n(none found)"
    )

    history_block = ""
    if history:
        lines = []
        for msg in history[-6:]:
            role = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"{role}: {msg['content']}")
        history_block = "\n### Conversation History\n" + "\n".join(lines)

    user_message = (
        f"### Text Chunks\n{chunk_block}\n\n"
        f"{graph_section}"
        f"{history_block}\n\n"
        f"### Question\n{question}"
    )

    try:
        response = await _llm().ainvoke([
            ("system", SYSTEM_PROMPT),
            ("human", user_message),
        ])
        answer = response.content
        reasoning.append("Answer synthesised successfully.")
    except Exception as e:
        logger.error(f"LLM synthesis failed: {e}", exc_info=True)
        answer = f"Error generating answer: {str(e)}"
        reasoning.append(f"LLM synthesis failed: {e}")

    return {**state, "answer": answer, "reasoning": reasoning}


# ── Build the graph ───────────────────────────────────────────────────────────

def build_agent() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("route", node_route)
    graph.add_node("retrieve", node_retrieve)
    graph.add_node("grade", node_grade)
    graph.add_node("synthesise", node_synthesise)

    graph.set_entry_point("route")
    graph.add_edge("route", "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        grade_router,
        {"retrieve": "retrieve", "synthesise": "synthesise"},
    )
    graph.add_edge("synthesise", END)

    return graph.compile()


_agent = build_agent()


# ── Public entry point ────────────────────────────────────────────────────────

async def run_agent(question: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    sid = get_or_create_session(session_id)
    history = await get_history(sid)          # ← await

    initial_state: AgentState = {
        "question": question,
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

    final_state = await _agent.ainvoke(initial_state)

    await append_to_history(sid, "user", question)           # ← await
    await append_to_history(sid, "assistant", final_state["answer"])  # ← await

    return {
        "answer": final_state["answer"],
        "session_id": sid,
        "sources": final_state.get("chunks", []),
        "graph_context": final_state.get("graph_ctx", ""),
        "reasoning": final_state.get("reasoning", []),
        "strategy": final_state.get("strategy", "both"),
    }