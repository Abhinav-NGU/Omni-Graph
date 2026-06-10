import logging
from typing import List, Dict, Any

from langchain_ollama.chat_models import ChatOllama
from langchain_ollama.embeddings import OllamaEmbeddings
from qdrant_client import AsyncQdrantClient

from core.config import settings
from core.db import db_manager
from ingestion import QDRANT_COLLECTION_NAME

logger = logging.getLogger(__name__)

TOP_K_CHUNKS = 5
MAX_GRAPH_RESULTS = 30


def _get_embedding_model() -> OllamaEmbeddings:
    return OllamaEmbeddings(
        model="nomic-embed-text",
        base_url=settings.OLLAMA_BASE_URL,
    )


def _get_llm() -> ChatOllama:
    return ChatOllama(
        model="llama3.2",   # ← change this
        base_url=settings.OLLAMA_BASE_URL,
        temperature=0.2,
    )

# ── Step 1: embed ─────────────────────────────────────────────────────────────

async def embed_query(question: str) -> List[float]:
    logger.info("Embedding query...")
    return await _get_embedding_model().aembed_query(question)


# ── Step 2: vector search ─────────────────────────────────────────────────────
MIN_SCORE = 0.4

async def vector_search(query_vector: List[float]) -> List[Dict[str, Any]]:
    logger.info(f"Searching Qdrant for top {TOP_K_CHUNKS} chunks...")
    client: AsyncQdrantClient = db_manager.qdrant_client

    # Check collection exists first
    try:
        response = await client.query_points(
            collection_name=QDRANT_COLLECTION_NAME,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        )
        results = response.points
    except Exception as e:
        logger.error(f"Qdrant search failed: {e}", exc_info=True)
        return []

    chunks = []
    for hit in results:
        text = (hit.payload or {}).get("text", "")
        if text:
            chunks.append({"id": str(hit.id), "text": text, "score": hit.score})

    # Sort highest score first
    chunks.sort(key=lambda x: x["score"], reverse=True)

    logger.info(f"Retrieved {len(chunks)} chunks (scores: {[round(c['score'], 3) for c in chunks]}).")
    return chunks


# ── Step 3: graph context ─────────────────────────────────────────────────────

async def _fetch_graph_tx(tx, entity_names: List[str]) -> List[str]:
    one_hop = """
    UNWIND $names AS name
    CALL db.index.fulltext.queryNodes('entity_name_index', name + '*')
    YIELD node AS e, score
    MATCH (e)-[r:RELATES_TO]->(n:Entity)
    WHERE r.type IS NOT NULL
    RETURN e.name AS src, r.type AS rel, n.name AS tgt
    ORDER BY score DESC
    LIMIT $limit
    """

    two_hop = """
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

    lines = []

    r1 = await tx.run(one_hop, names=entity_names, limit=MAX_GRAPH_RESULTS)
    for rec in await r1.data():
        lines.append(f"{rec['src']} --[{rec['rel']}]--> {rec['tgt']}")

    r2 = await tx.run(two_hop, names=entity_names, limit=MAX_GRAPH_RESULTS)
    for rec in await r2.data():
        lines.append(
            f"{rec['src']} --[{rec['rel1']}]--> {rec['mid']} --[{rec['rel2']}]--> {rec['tgt']}"
        )

    return list(dict.fromkeys(lines))  # deduplicate


async def graph_context(chunks: List[Dict[str, Any]]) -> str:
    if not chunks:
        return ""

    entity_names: List[str] = []
    for chunk in chunks:
        for token in chunk["text"].split():
            cleaned = token.strip(".,;:\"'()[]")
            if cleaned and cleaned[0].isupper() and len(cleaned) > 2:
                entity_names.append(cleaned)

    entity_names = list(dict.fromkeys(entity_names))[:30]

    if not entity_names:
        logger.info("No candidate entities found; skipping graph query.")
        return ""

    logger.info(f"Querying Neo4j for {len(entity_names)} candidate entities...")

    try:
        async with db_manager.neo4j_driver.session() as session:
            lines = await session.execute_read(_fetch_graph_tx, entity_names)
    except Exception as e:
        logger.error(f"Neo4j graph query failed: {e}", exc_info=True)
        return ""

    if not lines:
        logger.info("No graph paths found in Neo4j.")
        return ""

    logger.info(f"Graph context: {len(lines)} paths found.")
    return "\n".join(lines)


async def graph_context(chunks: List[Dict[str, Any]]) -> str:
    if not chunks:
        return ""

    entity_names: List[str] = []
    for chunk in chunks:
        text = chunk["text"]
        tokens = text.split()

        # Single capitalised words
        for token in tokens:
            cleaned = token.strip(".,;:\"'()[]")
            if cleaned and cleaned[0].isupper() and len(cleaned) > 2:
                entity_names.append(cleaned)

        # Consecutive capitalised words as phrases e.g. "Elon Musk", "SpaceX Inc."
        i = 0
        while i < len(tokens):
            cleaned = tokens[i].strip(".,;:\"'()[]")
            if cleaned and cleaned[0].isupper() and len(cleaned) > 2:
                phrase = [cleaned]
                j = i + 1
                while j < len(tokens):
                    next_cleaned = tokens[j].strip(".,;:\"'()[]")
                    if next_cleaned and next_cleaned[0].isupper() and len(next_cleaned) > 1:
                        phrase.append(next_cleaned)
                        j += 1
                    else:
                        break
                if len(phrase) > 1:
                    entity_names.append(" ".join(phrase))
                i = j
            else:
                i += 1

    entity_names = list(dict.fromkeys(entity_names))[:30]
    logger.info(f"Candidate entities extracted: {entity_names}")  # ← log so you can see what's found

    try:
        async with db_manager.neo4j_driver.session() as session:
            records = await session.execute_read(_fetch_graph_tx, entity_names)
    except Exception as e:
        logger.error(f"Neo4j graph query failed: {e}", exc_info=True)
        return ""

    if not records:
        logger.info("No graph paths found in Neo4j.")
        return ""

    lines = []
    for rec in records:
        node_names = rec.get("node_names", [])
        rel_types = rec.get("rel_types", [])
        parts = [node_names[0]] if node_names else []
        for rel, nxt in zip(rel_types, node_names[1:]):
            parts.append(f"--[{rel}]-->")
            parts.append(nxt)
        lines.append(" ".join(parts))

    logger.info(f"Graph context: {len(lines)} paths found.")
    return "\n".join(lines)


# ── Step 4: LLM synthesis ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a knowledgeable assistant with access to two sources of context:

1. Relevant text chunks retrieved via semantic search.
2. Knowledge-graph paths showing how entities relate to each other.

Use both sources to answer accurately and concisely.
If neither contains enough information, say so — do not fabricate facts.
"""


async def synthesise_answer(question: str, chunks: List[Dict[str, Any]], graph_ctx: str) -> str:
    logger.info("Synthesising answer with llama3...")

    chunk_block = "\n\n---\n\n".join(
        f"[Chunk {i+1} | score {c['score']:.3f}]\n{c['text']}"
        for i, c in enumerate(chunks)
    )
    graph_section = (
        f"### Graph Context\n{graph_ctx}" if graph_ctx
        else "### Graph Context\n(none found)"
    )

    user_message = f"### Text Chunks\n{chunk_block}\n\n{graph_section}\n\n### Question\n{question}"

    response = await _get_llm().ainvoke([
        ("system", SYSTEM_PROMPT),
        ("human", user_message),
    ])
    return response.content


# ── Public entry point ────────────────────────────────────────────────────────

async def run_query_pipeline(question: str) -> Dict[str, Any]:
    query_vector = await embed_query(question)
    chunks = await vector_search(query_vector)

    if not chunks:
        return {
            "answer": "I could not find any relevant information. Please ingest some documents first.",
            "sources": [],
            "graph_context": "",
        }

    graph_ctx = await graph_context(chunks)
    answer = await synthesise_answer(question, chunks, graph_ctx)

    return {"answer": answer, "sources": chunks, "graph_context": graph_ctx}