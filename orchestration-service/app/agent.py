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
from mcp_tools import call_tool


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
    question = state["question"].lower()
    reasoning = state.get("reasoning", [])

    # Questions that need live data — route to tool_call directly
    tool_signals = [
        "current time", "what time", "time now", "time is it",
        "what date", "today's date", "current date",
        "calculate", "compute", "solve",
        "multiply", "divide", "add", "subtract",
        "+ ", "- ", "* ", "/ ", "=",
        "convert", "timezone", "to ist", "to utc", "time in",
        "utc to", "ist to",
        "search", "web", "internet", "google", "look up"
    ]

    graph_signals = [
        "how does", "relate", "connection", "connected",
        "relationship", "between", "link", "path", "who works",
        "who founded", "who leads", "part of",
    ]

    vector_signals = [
        "explain", "describe", "summarise", "summary",
        "tell me about", "definition", "how does it work",
    ]

    is_tool = any(sig in question for sig in tool_signals)
    is_graph = any(sig in question for sig in graph_signals)
    is_vector = any(sig in question for sig in vector_signals)

    if is_tool and not is_graph and not is_vector:
        strategy = "tool_only"
        reasoning.append("Routed to: tool-only (live data or calculation question detected)")
    elif is_graph and not is_vector:
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

    # tool_only skips all retrieval — goes straight to grade which routes to tool_call
    if strategy == "tool_only":
        reasoning.append("Tool-only strategy — skipping knowledge base retrieval.")
        return {**state, "chunks": [], "graph_ctx": "", "reasoning": reasoning}

    if strategy in ("vector", "both"):
        chunks = await _vector_search(question)
        reasoning.append(f"Hybrid search returned {len(chunks)} chunks.")

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
    chunks = state.get("chunks", [])
    graph_ctx = state.get("graph_ctx", "")
    retries = state.get("retries", 0)
    strategy = state.get("strategy", "both")
    reasoning = state.get("reasoning", [])
    question = state["question"]

    # tool_only strategy — always insufficient, force tool_call
    if strategy == "tool_only":
        reasoning.append("Tool-only strategy — routing to tool call.")
        return {**state, "context_sufficient": False, "retries": MAX_RETRIES, "reasoning": reasoning}

    if not chunks and not graph_ctx:
        sufficient = False
    else:
        # Active LLM Grading
        context_text = "\n\n".join([c.get("text", "") for c in chunks])
        if graph_ctx:
            context_text += f"\n\nGraph Context:\n{graph_ctx}"
            
        prompt = f"""You are a strict grader. Determine if the provided context contains the answer to the user's question.

RULES:
- If the context contains the answer, output YES.
- If the context DOES NOT contain the answer, output NO.
- CRITICAL: If the question asks about a specific person (e.g., 'Abhinav Bindra') and the context is about someone else with the same first name (e.g., 'Abhinav Nair'), you MUST output NO. Do not assume typos.

Question: {question}

Context:
{context_text}

Output ONLY YES or NO."""
        
        try:
            response = await _llm().ainvoke([("human", prompt)])
            sufficient = "YES" in response.content.strip().upper()
        except Exception as e:
            logger.error(f"Grader LLM failed: {e}")
            sufficient = True

    if not sufficient and retries < MAX_RETRIES:
        reasoning.append(
            f"Context graded as insufficient (retry {retries + 1}/{MAX_RETRIES}). "
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
        reasoning.append("Context graded as insufficient after max retries — trying tools.")
    else:
        reasoning.append(
            f"Context graded as sufficient: {len(chunks)} chunks, "
            f"{'graph paths found' if graph_ctx else 'no graph paths'}."
        )

    return {**state, "context_sufficient": sufficient, "reasoning": reasoning}

def grade_router(state: AgentState) -> str:
    if not state.get("context_sufficient", True):
        if state.get("retries", 0) < MAX_RETRIES:
            return "retrieve"
        else:
            return "tool_call"   # ← exhausted retries, try web search
    return "synthesise"


# ========== STAGE 4: SYNTHESIS NODE ==========
# Uses the LLM to generate a final answer based on all collected context.

SYSTEM_PROMPT = """\
You are a knowledgeable assistant with access to:
1. Relevant text chunks retrieved via semantic search.
2. Knowledge-graph paths showing how entities relate to each other.
3. The conversation history with this user.

Use all available context to answer accurately and concisely.
You may use your internal knowledge for general facts and common sense.
Do NOT perform your own math or timezone calculations; strictly output the result provided in the context chunks or tool outputs.
ENTITY ALIASES: Understand that variations of a name (e.g., "Elon", "Elon Musk", "Elon R. Musk") usually refer to the same entity. However, entirely distinct full names (e.g., "Abhinav Nair" vs "Abhinav Bindra") are completely different people.
For domain-specific knowledge, if the context doesn't contain enough information, say so honestly — do not fabricate.
Do NOT use introductory phrases like "Based on the provided context" or "According to the information". Provide the answer straight.
CRITICAL: If a question is ambiguous or lacks necessary details (e.g., asking about a person using only a first name like "Abhinav"), you MUST politely ask the user to clarify (e.g., "Did you mean Abhinav Nair?"). Do not just output a bare name.
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
    # Uploaded PDFs (Attachments) bypass the 6-message sliding window.
    history_block = ""
    if history:
        attachments = [m for m in history if m["content"].startswith("[ATTACHMENT:") or m["content"].startswith("[System: User uploaded a PDF")]
        recent = [m for m in history if not (m["content"].startswith("[ATTACHMENT:") or m["content"].startswith("[System: User uploaded a PDF"))][-6:]
        
        lines = []
        for msg in attachments:
            lines.append(f"User uploaded document:\n{msg['content']}")
        for msg in recent:
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


from mcp_tools import call_tool

def execute_timezone_conversion(question: str) -> str:
    """Programmatic function to convert timezones or get current time in a timezone."""
    import re
    from datetime import datetime, timedelta

    tz_offsets = {
        "utc": 0, "gmt": 0, "ist": 5.5,
        "est": -5, "edt": -4, "pst": -8, "pdt": -7,
        "cst": -6, "cdt": -5, "mst": -7, "mdt": -6,
        "bst": 1, "cet": 1, "cest": 2, "aest": 10, "aedt": 11
    }
    
    question_lower = question.lower()
    found_tzs = []
    for word in re.findall(r'\b[a-z]{3,4}\b', question_lower):
        if word in tz_offsets and word not in found_tzs:
            found_tzs.append(word)
            
    time_match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', question_lower)
    
    # Explicit Conversion: e.g. "10:00 AM UTC to IST"
    if len(found_tzs) >= 2 and time_match:
        from_tz = found_tzs[0]
        to_tz = found_tzs[1]
        
        hour = int(time_match.group(1))
        minute = int(time_match.group(2)) if time_match.group(2) else 0
        ampm = time_match.group(3)
        
        if ampm:
            ampm = ampm.lower()
            if ampm == "pm" and hour < 12: hour += 12
            elif ampm == "am" and hour == 12: hour = 0
            
        diff_hours = tz_offsets[to_tz] - tz_offsets[from_tz]
        total_minutes = hour * 60 + minute + int(diff_hours * 60)
        
        normalized_minutes = total_minutes % (24 * 60)
        res_hour = normalized_minutes // 60
        res_minute = normalized_minutes % 60
        
        res_ampm = "AM" if res_hour < 12 else "PM"
        display_hour = res_hour if res_hour <= 12 else res_hour - 12
        if display_hour == 0: display_hour = 12
            
        original_time = f"{int(time_match.group(1)):02d}:{minute:02d} {ampm.upper() if ampm else ''}".strip()
        converted_time = f"{int(display_hour):02d}:{int(res_minute):02d} {res_ampm}"
        
        return f"Conversion Result: {original_time} {from_tz.upper()} is {converted_time} {to_tz.upper()} ({res_hour:02d}:{int(res_minute):02d} 24h format)."
    
    # Current Time Mapping: e.g. "What time is it in IST?"
    current_utc = datetime.utcnow()
    context = [f"Current UTC time: {current_utc.strftime('%Y-%m-%d %H:%M:%S')}"]
    for tz in found_tzs:
        if tz in ("utc", "gmt"): continue
        tz_time = current_utc + timedelta(hours=tz_offsets[tz])
        context.append(f"Current time in {tz.upper()}: {tz_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
    return "\n".join(context)

async def node_tool_call(state: AgentState) -> AgentState:
    """
    Calls the appropriate mcp-tools-service tool based on the question.
    Fired when knowledge base retrieval found nothing OR strategy is tool_only.
    """
    question = state["question"]
    question_lower = question.lower()
    reasoning = state.get("reasoning", [])

    import re

    # Detect which tool to use
    time_signals = ["current time", "what time", "time now", "time is it", "what date", "today"]
    calc_signals = ["calculate", "compute", "solve", "+ ", "- ", "* ", "/ ", "=",
                    "multiply", "divide", "add", "subtract"]
    tz_signals = ["convert", "timezone", "to ist", "to utc", "time in"]

    is_tz = any(sig in question_lower for sig in tz_signals)
    if not is_tz and re.search(r'\b(ist|utc|gmt|pst|est|cet|bst)\b', question_lower) and ("time" in question_lower or "convert" in question_lower):
        is_tz = True

    if is_tz:
        tool = "timezone_converter"
        tool_input = question
        reasoning.append("Tool selected: timezone_converter")
    elif any(sig in question_lower for sig in time_signals):
        tool = "current_time"
        tool_input = ""
        reasoning.append("Tool selected: current_time")
    elif any(sig in question_lower for sig in calc_signals):
        # Extract math expression — take everything after keywords
        tool = "calculator"
        for prefix in ["calculate", "compute", "solve"]:
            if prefix in question_lower:
                tool_input = question_lower.split(prefix, 1)[-1].strip().rstrip("?")
                break
        else:
            tool_input = question
        reasoning.append(f"Tool selected: calculator with input '{tool_input}'")
    else:
        tool = "web_search"
        tool_input = question
        reasoning.append(f"Tool selected: web_search for '{question[:50]}'")

    if tool == "timezone_converter":
        logger.info(f"Executing local timezone conversion for: '{question}'")
        result_text = execute_timezone_conversion(question)
        result = {"answer": result_text}
    else:
        logger.info(f"Calling tool '{tool}' with input: '{tool_input}'")
        result = await call_tool(tool, tool_input)

    if "error" in result and result["error"]:
        reasoning.append(f"Tool '{tool}' failed: {result['error']}")
        return {**state, "context_sufficient": True, "reasoning": reasoning}

    # Format result as a synthetic chunk
    result_text = _format_tool_result(tool, result)
    synthetic_chunk = {
        "id": f"tool_{tool}",
        "text": f"[Tool: {tool}]\n{result_text}",
        "score": 1.0,
        "in_vector": False,
        "in_bm25": False,
    }

    reasoning.append(f"Tool '{tool}' returned a result successfully.")
    return {
        **state,
        "chunks": [synthetic_chunk],
        "context_sufficient": True,
        "reasoning": reasoning,
    }


def _format_tool_result(tool: str, result: dict) -> str:
    """Format tool results into readable text for the LLM."""
    if tool == "timezone_converter":
        return result.get("answer", "")
    elif tool == "current_time":
        return (
            f"Current time: {result.get('utc', 'unknown')} UTC\n"
            f"Date: {result.get('date', 'unknown')}\n"
            f"Time: {result.get('time', 'unknown')} UTC"
        )
    elif tool == "calculator":
        return (
            f"Expression: {result.get('expression', 'unknown')}\n"
            f"Result: {result.get('result', 'unknown')}"
        )
    elif tool == "web_search":
        answer = result.get("answer", "")
        if not answer or not str(answer).strip():
            return "No web search results found."
        source = result.get("source", "")
        url = result.get("url", "")
        text = f"Search result: {answer}"
        if source:
            text += f"\nSource: {source}"
        if url:
            text += f"\nURL: {url}"
        return text
    else:
        return str(result)


# ========== LANGGRAPH COMPILATION ==========
# Assemble the state machine and define transition rules between nodes.

def build_agent() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("route", node_route)
    graph.add_node("retrieve", node_retrieve)
    graph.add_node("grade", node_grade)
    graph.add_node("tool_call", node_tool_call)
    graph.add_node("synthesise", node_synthesise)

    graph.set_entry_point("route")
    graph.add_edge("route", "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        grade_router,
        {
            "retrieve": "retrieve",
            "tool_call": "tool_call",
            "synthesise": "synthesise",
        },
    )
    graph.add_edge("tool_call", "synthesise")
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