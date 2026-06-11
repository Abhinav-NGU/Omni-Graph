"""
query.py — Phase 2 + 8c: RAG + Graph Query Pipeline with Hybrid Search
"""

import logging
from typing import List, Dict, Any

from langchain_ollama.chat_models import ChatOllama
from langchain_ollama.embeddings import OllamaEmbeddings

from core.config import settings
from core.db import db_manager
from ingestion import QDRANT_COLLECTION_NAME
from hybrid_search import hybrid_search
from compressor import compress_chunks

logger = logging.getLogger(__name__)

TOP_K_CHUNKS = 5
MAX_GRAPH_RESULTS = 30


def _get_llm() -> ChatOllama:
    return ChatOllama(
        model="llama3.2",
        base_url=settings.OLLAMA_BASE_URL,
        temperature=0.2,
    )


# ── Step 1: hybrid search ─────────────────────────────────────────────────────

async def search_chunks(question: str) -> List[Dict[str, Any]]:
    """Hybrid search — BM25 + vector + RRF merge."""
    chunks = await hybrid_search(question)
    logger.info(
        f"Hybrid search returned {len(chunks)} chunks: "
        f"{[round(c['score'], 4) for c in chunks]}"
    )
    return chunks


# ── Step 2: graph context ─────────────────────────────────────────────────────

async def _fetch_graph_tx(tx, entity_names: List[str]) -> List[str]:
    one_hop = """
    UNWIND $names AS name
    MATCH (e:Entity)-[r:RELATES_TO]->(n:Entity)
    WHERE toLower(e.name) CONTAINS toLower(name)
      AND r.type IS NOT NULL
    RETURN e.name AS src, r.type AS rel, n.name AS tgt
    LIMIT $limit
    """
    two_hop = """
    UNWIND $names AS name
    MATCH (e:Entity)-[r1:RELATES_TO]->(mid:Entity)-[r2:RELATES_TO]->(n:Entity)
    WHERE toLower(e.name) CONTAINS toLower(name)
      AND r1.type IS NOT NULL AND r2.type IS NOT NULL
    RETURN e.name AS src, r1.type AS rel1, mid.name AS mid,
           r2.type AS rel2, n.name AS tgt
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

    return list(dict.fromkeys(lines))


async def graph_context(chunks: List[Dict[str, Any]]) -> str:
    if not chunks:
        return ""

    entity_names: List[str] = []
    for chunk in chunks:
        tokens = chunk["text"].split()
        for i, token in enumerate(tokens):
            cleaned = token.strip(".,;:\"'()[]")
            if cleaned and cleaned[0].isupper() and len(cleaned) > 2:
                entity_names.append(cleaned)
            # Also grab two-word phrases
            if i < len(tokens) - 1:
                next_cleaned = tokens[i+1].strip(".,;:\"'()[]")
                if (cleaned and cleaned[0].isupper() and
                        next_cleaned and next_cleaned[0].isupper()):
                    entity_names.append(f"{cleaned} {next_cleaned}")

    entity_names = list(dict.fromkeys(entity_names))[:30]

    if not entity_names:
        logger.info("No candidate entities found; skipping graph query.")
        return ""

    logger.info(f"Querying Neo4j for {len(entity_names)} entities...")

    try:
        async with db_manager.neo4j_driver.session() as session:
            lines = await session.execute_read(_fetch_graph_tx, entity_names)
    except Exception as e:
        logger.error(f"Neo4j graph query failed: {e}", exc_info=True)
        return ""

    if not lines:
        logger.info("No graph paths found.")
        return ""

    logger.info(f"Graph context: {len(lines)} paths found.")
    return "\n".join(lines)


# ── Step 3: LLM synthesis ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a knowledgeable assistant with access to two sources of context:

1. Relevant text chunks retrieved via hybrid search (semantic + keyword).
2. Knowledge-graph paths showing how entities relate to each other.

Use both sources to answer accurately and concisely.
If neither contains enough information, say so honestly — do not fabricate facts.
"""


async def synthesise_answer(
    question: str,
    chunks: List[Dict[str, Any]],
    graph_ctx: str,
) -> str:
    logger.info("Synthesising answer with llama3.2...")

    chunk_block = "\n\n---\n\n".join(
        f"[Chunk {i+1} | score {c['score']:.4f}"
        f"{' | vector+bm25' if c.get('in_vector') and c.get('in_bm25') else ''}]\n{c['text']}"
        for i, c in enumerate(chunks)
    )

    graph_section = (
        f"### Knowledge-Graph Context\n{graph_ctx}"
        if graph_ctx else "### Knowledge-Graph Context\n(none found)"
    )

    user_message = (
        f"### Retrieved Text Chunks\n{chunk_block}\n\n"
        f"{graph_section}\n\n"
        f"### Question\n{question}"
    )

    response = await _get_llm().ainvoke([
        ("system", SYSTEM_PROMPT),
        ("human", user_message),
    ])
    return response.content


async def search_node_for_visual(node_name: str) -> Dict[str, Any]:
    """
    Searches for a node by name and returns its 1-hop neighborhood
    in a format suitable for graph visualization libraries.
    """
    # Using a directed match for clarity in the graph
    cypher_query = """
    MATCH (n:Entity)
    WHERE toLower(n.name) CONTAINS toLower($name)
    WITH n
    LIMIT 10 // Max source nodes to expand from
    OPTIONAL MATCH (n)-[r:RELATES_TO]->(m:Entity)
    RETURN n, m, properties(r) as r_props
    LIMIT 50 // Max total relationships
    """

    async def _tx(tx):
        result = await tx.run(cypher_query, name=node_name)
        records = await result.data()

        nodes_dict = {}
        edges_set = set()

        for record in records:
            source_node = record.get("n")
            rel_props = record.get("r_props")
            target_node = record.get("m")

            if not source_node:
                continue

            source_name = source_node.get('name')
            if source_name not in nodes_dict:
                nodes_dict[source_name] = {"id": source_name, "label": source_name}

            if rel_props and target_node:
                target_name = target_node.get('name')
                if target_name not in nodes_dict:
                    nodes_dict[target_name] = {"id": target_name, "label": target_name}

                rel_type = rel_props.get("type")
                if rel_type:
                    edges_set.add((source_name, target_name, rel_type))

        final_nodes = list(nodes_dict.values())
        final_edges = [{"from": s, "to": t, "label": l} for s, t, l in edges_set]

        return {"nodes": final_nodes, "edges": final_edges}

    async with db_manager.neo4j_driver.session() as session:
        return await session.execute_read(_tx)

# ── Public entry point ────────────────────────────────────────────────────────

async def run_query_pipeline(question: str) -> Dict[str, Any]:
    chunks = await search_chunks(question)

    if not chunks:
        return {
            "answer": "I could not find any relevant information. Please ingest documents first.",
            "sources": [],
            "graph_context": "",
        }

    # Phase 8d — compress chunks to only relevant sentences
    compressed = await compress_chunks(question, chunks)

    # Fall back to original chunks if compression dropped everything
    final_chunks = compressed if compressed else chunks

    graph_ctx = await graph_context(final_chunks)
    answer = await synthesise_answer(question, final_chunks, graph_ctx)

    return {
        "answer": answer,
        "sources": chunks,          # return original sources for UI display
        "graph_context": graph_ctx,
    }