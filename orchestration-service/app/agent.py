"""
agent.py — Phase 3: LangGraph Agentic Pipeline

Replaces the linear Phase 2 query pipeline with a smart agent that:
  1. Routes the question (vector-only / graph-only / both)
  2. Retrieves from the appropriate sources
  3. Grades the retrieved context — if weak, retries with a broader strategy
  4. Synthesises a final grounded answer
  5. Tracks full conversation history per session (in-memory)

HOW IT WORKS:
=============
This module implements an agentic RAG system using LangGraph (a state machine library).
The agent processes a user's question through multiple stages:

STAGE 1 (ROUTE): Analyze the question to determine retrieval strategy
                 - Graph queries for relational questions (e.g., "how does X relate to Y")
                 - Vector search for descriptive questions (e.g., "what is X")
                 - Both for complex/ambiguous questions

STAGE 2 (RETRIEVE): Fetch context from the chosen source(s)
                    - Vector search: Semantic similarity from Qdrant vector DB
                    - Graph search: Entity relationships from Neo4j knowledge graph

STAGE 3 (GRADE): Evaluate if retrieved context is sufficient
                 - If poor, retry with broader strategy (e.g., switch from vector-only to both)
                 - If still poor after max retries, proceed with what we have
                 - Otherwise, move to synthesis

STAGE 4 (SYNTHESISE): Use LLM to generate a grounded answer
                      - Combines text chunks + graph paths + conversation history
                      - LLM generates response based on all available context

PERSISTENCE: Conversation history per session is stored in Redis for multi-turn context.
"""

import logging
import uuid
from typing import List, Dict, Any, Optional, Literal
import json

from langchain_ollama.chat_models import ChatOllama
from langchain_ollama.embeddings import OllamaEmbeddings
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict
from hybrid_search import hybrid_search as _hybrid_search
from compressor import compress_chunks

from core.config import settings
from core.db import db_manager
from ingestion import QDRANT_COLLECTION_NAME

logger = logging.getLogger(__name__)

# ========== CONFIGURATION CONSTANTS ==========
TOP_K = 5                    # Number of vector search results to retrieve
MIN_SCORE = 0.35            # Minimum similarity score threshold for vector results
MAX_GRAPH_RESULTS = 25      # Maximum relationships to retrieve from Neo4j per query
MAX_RETRIES = 2             # Max retry attempts if context is insufficient


# ========== REDIS-BACKED SESSION MANAGEMENT ==========
# Maintains conversation history across multiple requests in the same session.
# Each session ID maps to a list of (role, content) message pairs.

SESSION_TTL = 60 * 60 * 24  # 24 hours — sessions expire after a day of inactivity
SESSION_PREFIX = "omnigraph:session:"


def _session_key(session_id: str) -> str:
    """Generate Redis key for a session ID."""
    return f"{SESSION_PREFIX}{session_id}"


def get_or_create_session(session_id: Optional[str]) -> str:
    """Return existing session ID or generate a new UUID-based one."""
    return session_id or str(uuid.uuid4())


async def get_history(session_id: str) -> List[Dict[str, str]]:
    """
    Retrieve conversation history for a session from Redis.
    
    Returns:
        List of messages as dicts with 'role' ('user' or 'assistant') and 'content'
    """
    redis = db_manager.redis
    raw = await redis.get(_session_key(session_id))
    if not raw:
        return []
    return json.loads(raw)


async def append_to_history(session_id: str, role: str, content: str):
    """
    Append a new message to session history in Redis.
    
    Args:
        session_id: Session identifier
        role: 'user' or 'assistant'
        content: Message text
    """
    redis = db_manager.redis
    key = _session_key(session_id)
    history = await get_history(session_id)
    history.append({"role": role, "content": content})
    await redis.set(key, json.dumps(history), ex=SESSION_TTL)


async def clear_session(session_id: str):
    """Delete all history for a session."""
    redis = db_manager.redis
    await redis.delete(_session_key(session_id))


# ========== LANGGRAPH STATE DEFINITION ==========
# AgentState represents the complete state of the agent at any point in its execution.
# Each node receives this state, processes it, and returns an updated version.
# LangGraph manages the flow between nodes based on routing logic.

class AgentState(TypedDict):
    """
    State object passed through the agent graph.
    
    Attributes:
        question: The original user question
        session_id: Unique identifier for this conversation session
        history: List of previous user/assistant messages in this session
        strategy: Retrieval strategy ('vector', 'graph', or 'both')
        chunks: List of text chunks retrieved via vector search
        graph_ctx: String containing Neo4j relationship paths
        context_sufficient: Boolean flag indicating if retrieved context is adequate
        retries: Count of retry attempts if context was insufficient
        answer: The final LLM-generated answer
        reasoning: List of reasoning steps and debug info
    """
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


# ========== LLM & EMBEDDING MODEL HELPERS ==========
# Factory functions to instantiate the language models used throughout the agent.

def _llm() -> ChatOllama:
    """
    Initialize the language model (Llama 3.2).
    Used for reasoning, routing, and answer synthesis.
    
    Returns:
        ChatOllama instance configured for low-temperature responses (more deterministic)
    """
    return ChatOllama(
        model="llama3.2",
        base_url=settings.OLLAMA_BASE_URL,
        temperature=0.2,  # Low temperature for consistent, deterministic behavior
    )


def _embedder() -> OllamaEmbeddings:
    """
    Initialize the embedding model (Nomic Embed Text).
    Used to embed user questions and find similar chunks from Qdrant.
    
    Returns:
        OllamaEmbeddings instance configured for vector search
    """
    return OllamaEmbeddings(
        model="nomic-embed-text",
        base_url=settings.OLLAMA_BASE_URL,
    )


# ========== STAGE 1: ROUTING NODE ==========
# Decides which retrieval strategy to use based on the question type.
# Strategy determines which databases (or both) will be queried in the retrieval stage.

async def node_route(state: AgentState) -> AgentState:
    """
    Route node: Classify the question to determine retrieval strategy.
    
    Uses simple keyword matching to categorize:
    - Graph questions: Ask about relationships, connections, links between entities
    - Vector questions: Ask for explanations, definitions, descriptions
    - Ambiguous: Use both strategies for complex questions
    
    Args:
        state: Current agent state
    
    Returns:
        Updated state with 'strategy' field set and reasoning appended
    """
    question = state["question"].lower()
    reasoning = state.get("reasoning", [])

    # Keywords indicating relationship/entity-centric questions
    graph_signals = [
        "how does", "relate", "connection", "connected",
        "relationship", "between", "link", "path", "who works",
        "who founded", "who leads", "part of",
    ]
    
    # Keywords indicating descriptive/definitional questions
    vector_signals = [
        "what is", "explain", "describe", "summarise", "summary",
        "tell me about", "definition", "how does it work",
    ]

    is_graph = any(sig in question for sig in graph_signals)
    is_vector = any(sig in question for sig in vector_signals)

    # Determine strategy: if both signals found, use both; otherwise specific strategy
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


# ========== STAGE 2: RETRIEVAL NODE ==========
# Fetches context from the selected source(s) using two retrieval strategies.

async def _vector_search(question: str) -> List[Dict[str, Any]]:
    """Hybrid search — BM25 + vector + RRF. Replaces pure vector search."""
    try:
        results = await _hybrid_search(question)
        logger.info(f"Hybrid search returned {len(results)} chunks.")
        return results
    except Exception as e:
        logger.error(f"Hybrid search failed: {e}", exc_info=True)
        return []
    

async def _graph_search(question: str, broad: bool = False) -> str:
    """
    Retrieve entity relationships and paths from Neo4j knowledge graph.
    
    Steps:
    1. Extract entity names and keywords from the question (filter stopwords)
    2. Query 1-hop paths (e.g., A → B)
    3. Query 2-hop paths (e.g., A → B → C)
    4. Deduplicate and return as string representation
    
    Args:
        question: User's question text
        broad: If True, include multi-word phrases in entity extraction (for retries)
    
    Returns:
        String containing relationship paths, one per line. Format: "src --[rel]--> tgt"
    """
    # Extract entity names from question, filtering common stopwords
    stop_words = {
        "the", "and", "for", "with", "from", "that", "this",
        "what", "who", "how", "does", "did", "was", "are",
        "is", "in", "of", "to", "a", "an", "me", "about",
    }
    tokens = question.split()
    entity_names = []
    
    # Extract single words that might be entity names
    for token in tokens:
        cleaned = token.strip(".,;:\"'?!()")
        if cleaned and len(cleaned) > 2 and cleaned.lower() not in stop_words:
            entity_names.append(cleaned)

    # On retries, also try multi-word phrases
    if broad:
        for i in range(len(tokens) - 1):
            phrase = f"{tokens[i].strip('.,;')} {tokens[i+1].strip('.,;')}"
            entity_names.append(phrase)

    # Remove duplicates, limit to 20 candidates
    entity_names = list(dict.fromkeys(entity_names))[:20]

    if not entity_names:
        return ""

    logger.info(f"Graph search: querying for {entity_names}")

    # Two Cypher queries:
    # 1. One-hop: Entity A → Entity B
    # 2. Two-hop: Entity A → Entity B → Entity C
    # This shows direct and second-degree relationships.
    one_hop_cypher = """
    UNWIND $names AS name
    CALL db.index.fulltext.queryNodes('entity_name_index', name + '*')
    YIELD node AS e, score
    MATCH (e)-[r:RELATES_TO]->(n:Entity)
    WHERE r.type IS NOT NULL
    RETURN e.name AS src, r.type AS rel, n.name AS tgt
    ORDER BY score DESC
    LIMIT $limit
    """

    two_hop_cypher = """
    UNWIND $names AS name
    CALL db.index.fulltext.queryNodes('entity_name_index', name + '*')
    YIELD node AS e, score
    MATCH (e)-[r1:RELATES_TO]->(mid:Entity)-[r2:RELATES_TO]->(n:Entity)
    WHERE r1.type IS NOT NULL AND r2.type IS NOT NULL
    RETURN e.name AS src, r1.type AS rel1, mid.name AS mid,
        r2.type AS rel2, n.name AS tgt
    ORDER BY score DESC
    LIMIT $limit
    """

    try:
        async def _tx(tx):
            """Transaction handler: execute both Cypher queries and collect results."""
            lines = []

            # Execute 1-hop query
            r1 = await tx.run(one_hop_cypher, names=entity_names, limit=MAX_GRAPH_RESULTS)
            for rec in await r1.data():
                lines.append(f"{rec['src']} --[{rec['rel']}]--> {rec['tgt']}")

            # Execute 2-hop query
            r2 = await tx.run(two_hop_cypher, names=entity_names, limit=MAX_GRAPH_RESULTS)
            for rec in await r2.data():
                lines.append(
                    f"{rec['src']} --[{rec['rel1']}]--> {rec['mid']} --[{rec['rel2']}]--> {rec['tgt']}"
                )

            return lines

        # Run queries in a Neo4j session
        async with db_manager.neo4j_driver.session() as session:
            lines = await session.execute_read(_tx)

        # Deduplicate lines while preserving order
        lines = list(dict.fromkeys(lines))

        if not lines:
            return ""

        logger.info(f"Graph search: {len(lines)} paths found.")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Graph search failed: {e}", exc_info=True)
        return ""


async def node_retrieve(state: AgentState, compress: bool = True) -> AgentState:
    strategy = state["strategy"]
    question = state["question"]
    retries = state.get("retries", 0)
    reasoning = state.get("reasoning", [])
    broad = retries > 0

    chunks: List[Dict[str, Any]] = []
    graph_ctx = ""

    if strategy in ("vector", "both"):
        chunks = await _vector_search(question)
        reasoning.append(f"Hybrid search returned {len(chunks)} chunks.")

        # Only compress when not streaming — avoids conflicting Ollama calls
        if chunks and compress:
            from compressor import compress_chunks
            compressed = await compress_chunks(question, chunks)
            if compressed:
                reduction = round(
                    (1 - sum(len(c["text"]) for c in compressed) /
                     max(sum(len(c["text"]) for c in chunks), 1)) * 100
                )
                reasoning.append(
                    f"Contextual compression: {len(chunks)} → {len(compressed)} chunks "
                    f"({reduction}% reduction)."
                )
                chunks = compressed
            else:
                reasoning.append("Compression dropped all chunks — using originals.")

    if strategy in ("graph", "both"):
        graph_ctx = await _graph_search(question, broad=broad)
        reasoning.append(
            f"Graph search returned {'paths' if graph_ctx else 'no paths'}."
        )

    return {**state, "chunks": chunks, "graph_ctx": graph_ctx, "reasoning": reasoning}


# ========== STAGE 3: GRADING NODE ==========
# Evaluates whether retrieved context is sufficient for answer synthesis.
# If insufficient, triggers a retry with an expanded strategy (e.g., vector-only → both).

async def node_grade(state: AgentState) -> AgentState:
    """
    Grading node: Assess if context quality is sufficient.
    
    Logic:
    - If no chunks AND no graph paths: context insufficient
      - If retries < MAX_RETRIES: Set strategy to 'both' and increment retries
      - If retries >= MAX_RETRIES: Proceed with best effort (empty context)
    - Otherwise: Mark context as sufficient
    
    Args:
        state: Current agent state with 'chunks' and 'graph_ctx'
    
    Returns:
        Updated state with 'context_sufficient' flag and possibly escalated strategy
    """
    chunks = state.get("chunks", [])
    graph_ctx = state.get("graph_ctx", "")
    retries = state.get("retries", 0)
    reasoning = state.get("reasoning", [])

    # Determine if context is sufficient: at least chunks OR graph paths
    sufficient = bool(chunks) or bool(graph_ctx)

    # If insufficient and retries available: escalate strategy and retry retrieval
    if not sufficient and retries < MAX_RETRIES:
        reasoning.append(
            f"Context insufficient (retry {retries + 1}/{MAX_RETRIES}). "
            "Escalating to 'both'."
        )
        return {
            **state,
            "context_sufficient": False,
            "strategy": "both",  # Escalate to combined retrieval
            "retries": retries + 1,
            "reasoning": reasoning,
        }

    # If insufficient after max retries: give up and proceed
    if not sufficient:
        reasoning.append("Context still insufficient after max retries.")
    else:
        reasoning.append(
            f"Context sufficient: {len(chunks)} chunks, "
            f"{'graph paths found' if graph_ctx else 'no graph paths'}."
        )

    return {**state, "context_sufficient": True, "reasoning": reasoning}


def grade_router(state: AgentState) -> str:
    """
    Routing function: Decide next step after grading.
    
    Returns:
    - "retrieve": Go back to retrieval node (context was insufficient, retrying)
    - "synthesise": Proceed to answer synthesis (context is sufficient or max retries reached)
    """
    if not state.get("context_sufficient", True):
        return "retrieve"  # Retry retrieval
    return "synthesise"  # Proceed to synthesis


# ========== STAGE 4: SYNTHESIS NODE ==========
# Uses the LLM to generate a final answer based on all collected context.

SYSTEM_PROMPT = """\
You are a knowledgeable assistant with access to:
1. Relevant text chunks retrieved via semantic search.
2. Knowledge-graph paths showing how entities relate to each other.
3. The conversation history with this user.

Use all available context to answer accurately and concisely.
If the context doesn't contain enough information, say so honestly — do not fabricate.
"""


async def node_synthesise(state: AgentState) -> AgentState:
    """
    Synthesis node: Generate final answer using LLM.
    
    Constructs a comprehensive prompt containing:
    1. Text chunks from vector search (with scores)
    2. Entity relationships from graph search
    3. Recent conversation history (last 6 messages)
    4. The original user question
    
    Then invokes LLM to generate a grounded, contextual answer.
    
    Args:
        state: Agent state with chunks, graph_ctx, history, and question
    
    Returns:
        Updated state with 'answer' field populated by LLM
    """
    question = state["question"]
    chunks = state.get("chunks", [])
    graph_ctx = state.get("graph_ctx", "")
    history = state.get("history", [])
    reasoning = state.get("reasoning", [])

    # ─────── Format 1: Text chunks ───────
    # Display each chunk with its relevance score for transparency
    chunk_block = (
        "\n\n---\n\n".join(
            f"[Chunk {i+1} | score {c['score']:.3f}]\n{c['text']}"
            for i, c in enumerate(chunks)
        )
        if chunks else "(no vector context retrieved)"
    )

    # ─────── Format 2: Graph relationships ───────
    graph_section = (
        f"### Graph Paths\n{graph_ctx}"
        if graph_ctx else "### Graph Paths\n(none found)"
    )

    # ─────── Format 3: Conversation history ───────
    # Include last 6 messages for context (avoid token limit issues)
    history_block = ""
    if history:
        lines = []
        for msg in history[-6:]:  # Last 6 messages (3 turns)
            role = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"{role}: {msg['content']}")
        history_block = "\n### Conversation History\n" + "\n".join(lines)

    # ─────── Combine into full prompt ───────
    user_message = (
        f"### Text Chunks\n{chunk_block}\n\n"
        f"{graph_section}"
        f"{history_block}\n\n"
        f"### Question\n{question}"
    )

    try:
        # Invoke LLM with system prompt + full context
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


# ========== LANGGRAPH COMPILATION ==========
# Assemble the state machine and define transition rules between nodes.

def build_agent() -> StateGraph:
    """
    Construct the LangGraph state machine.
    
    Graph structure:
                    ┌──────────────┐
                    │   route      │ (Determine strategy)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  retrieve    │ (Fetch context)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    grade     │ (Check sufficiency)
                    └──────┬───────┘
                           │
                    [grade_router]
                    /           \
               "retrieve"    "synthesise"
                /                  \
            (RETRY)          ┌──────▼────────┐
                            │  synthesise    │ (Generate answer)
                            └──────┬────────┘
                                   │
                                 [END]
    
    Returns:
        Compiled StateGraph ready for invocation
    """
    graph = StateGraph(AgentState)

    # Add all nodes
    graph.add_node("route", node_route)
    graph.add_node("retrieve", node_retrieve)
    graph.add_node("grade", node_grade)
    graph.add_node("synthesise", node_synthesise)

    # Set entry point
    graph.set_entry_point("route")
    
    # Define sequential edges
    graph.add_edge("route", "retrieve")      # Route → Retrieve
    graph.add_edge("retrieve", "grade")      # Retrieve → Grade
    
    # Conditional edge: after grading, router function determines next node
    graph.add_conditional_edges(
        "grade",
        grade_router,
        {"retrieve": "retrieve", "synthesise": "synthesise"},  # Retry or proceed
    )
    
    # Synthesis leads to end
    graph.add_edge("synthesise", END)

    return graph.compile()


# Instantiate the compiled agent at module load time
_agent = build_agent()


# ========== PUBLIC ENTRY POINT ==========

async def run_agent(question: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Execute the agentic pipeline end-to-end.
    
    High-level flow:
    1. Get or create session ID (UUID if not provided)
    2. Retrieve conversation history for context
    3. Initialize agent state with question and history
    4. Execute the graph (route → retrieve → grade → [retry?] → synthesise)
    5. Append question and answer to Redis history
    6. Return final answer, sources, reasoning trace, and session ID
    
    Args:
        question: User's natural language question
        session_id: Optional session ID for multi-turn conversations
    
    Returns:
        Dict containing:
        - answer: Final LLM-generated response
        - session_id: Session ID for this conversation
        - sources: List of relevant text chunks (with scores)
        - graph_context: Entity relationships that informed the answer
        - reasoning: Step-by-step trace of the agent's logic
        - strategy: Retrieval strategy used ('vector', 'graph', or 'both')
    """
    # Get or create session
    sid = get_or_create_session(session_id)
    history = await get_history(sid)          # Retrieve prior messages

    # Initialize state machine with the question and conversation context
    initial_state: AgentState = {
        "question": question,
        "session_id": sid,
        "history": history,
        "strategy": "both",              # Default; will be overridden by router
        "chunks": [],                    # Will be populated by retrieval
        "graph_ctx": "",                 # Will be populated by retrieval
        "context_sufficient": False,     # Will be evaluated by grader
        "retries": 0,                    # Retry counter for context grading
        "answer": "",                    # Will be populated by synthesis
        "reasoning": [],                 # Trace of all decisions made
    }

    # Execute the graph — returns final state after all transitions
    final_state = await _agent.ainvoke(initial_state)

    # Persist the exchange in Redis history
    await append_to_history(sid, "user", question)
    await append_to_history(sid, "assistant", final_state["answer"])

    # Return structured result to caller
    return {
        "answer": final_state["answer"],
        "session_id": sid,
        "sources": final_state.get("chunks", []),
        "graph_context": final_state.get("graph_ctx", ""),
        "reasoning": final_state.get("reasoning", []),
        "strategy": final_state.get("strategy", "both"),
    }