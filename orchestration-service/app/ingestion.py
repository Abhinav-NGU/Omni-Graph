import asyncio
import logging
import uuid
from typing import List, Dict, Any
import hashlib
import json
import re


from langchain_ollama.chat_models import ChatOllama
from langchain_ollama.embeddings import OllamaEmbeddings
# from langchain_text_splitters import RecursiveCharacterTextSplitter
from chunker import semantic_chunk
from qdrant_client import models, AsyncQdrantClient

from core.config import settings
from core.db import db_manager
from graph import ExtractedGraph

logger = logging.getLogger(__name__)

QDRANT_COLLECTION_NAME = "omnigraph_chunks"


def _get_embedding_model() -> OllamaEmbeddings:
    return OllamaEmbeddings(model="nomic-embed-text", base_url=settings.OLLAMA_BASE_URL)


def _get_graph_extraction_llm() -> ChatOllama:
    return ChatOllama(
        model="llama3.2",
        base_url=settings.OLLAMA_BASE_URL,
        temperature=0.0,
    ).with_structured_output(ExtractedGraph)


async def chunk_text(text: str) -> List[str]:
    """
    Semantic chunking — splits on topic boundaries instead of
    fixed character count. Falls back gracefully for short texts.
    """
    return await semantic_chunk(text)


async def generate_embeddings(chunks: List[str]) -> List[List[float]]:
    logger.info(f"Generating embeddings for {len(chunks)} chunks...")
    return await _get_embedding_model().aembed_documents(chunks)



async def extract_graph_from_chunk(chunk: str) -> ExtractedGraph:
    logger.info("Extracting graph from chunk...")

    # Try structured output first
    try:
        llm = _get_graph_extraction_llm()
        result = await llm.ainvoke(f"""
You are an expert data analyst. Extract a knowledge graph from the text below.
Each relationship needs: 'source', 'target', 'relationship' (uppercase verb), 'properties'.

Example Output:
{{"entities": [{{"source": "Apple Inc.", "target": "Cupertino", "relationship": "BASED_IN", "properties": {{}}}}]}}

Text:
---
{chunk}
---
Respond ONLY with valid JSON. No explanation, no markdown.
        """)
        logger.info(f"Structured extraction got {len(result.entities)} entities.")
        return result
    except Exception as e:
        logger.warning(f"Structured output failed ({e}), trying manual parse...")

    # Fallback — plain LLM + manual JSON parse
    try:
        plain_llm = ChatOllama(
            model="llama3.2",
            base_url=settings.OLLAMA_BASE_URL,
            temperature=0.0,
        )
        response = await plain_llm.ainvoke(f"""
Extract a knowledge graph from this text. Return ONLY JSON, no other text:
{{"entities": [{{"source": "X", "target": "Y", "relationship": "VERB", "properties": {{}}}}]}}

Text:
---
{chunk}
---
        """)
        raw = re.sub(r"```(?:json)?|```", "", response.content).strip()
        data = json.loads(raw)
        # Pre-validate and filter entities to prevent Pydantic errors
        # from malformed LLM output (e.g., empty dicts in the list).
        if "entities" in data and isinstance(data["entities"], list):
            data["entities"] = [
                e for e in data["entities"]
                if isinstance(e, dict) and e.get("source") and e.get("target") and e.get("relationship")
            ]

        result = ExtractedGraph(**data)
        logger.info(f"Manual parse got {len(result.entities)} entities.")
        return result
    except Exception as e:
        logger.error(f"Manual parse also failed: {e}")
        return ExtractedGraph(entities=[])

async def _ensure_qdrant_collection(qdrant_client: AsyncQdrantClient, embedding_size: int):
    try:
        await qdrant_client.get_collection(collection_name=QDRANT_COLLECTION_NAME)
        logger.info(f"Qdrant collection '{QDRANT_COLLECTION_NAME}' already exists.")
    except Exception:
        logger.info(f"Creating Qdrant collection '{QDRANT_COLLECTION_NAME}'...")
        await qdrant_client.create_collection(
            collection_name=QDRANT_COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=embedding_size,
                distance=models.Distance.COSINE,
            ),
        )


async def upsert_chunks_to_qdrant(
    chunk_ids: List[str], chunks: List[str], embeddings: List[List[float]]
):
    if not embeddings:
        logger.warning("No embeddings — skipping Qdrant upsert.")
        return

    logger.info(f"Upserting {len(chunks)} chunks to Qdrant...")
    client: AsyncQdrantClient = db_manager.qdrant_client
    await _ensure_qdrant_collection(client, embedding_size=len(embeddings[0]))

    await client.upsert(
        collection_name=QDRANT_COLLECTION_NAME,
        points=models.Batch(
            ids=chunk_ids,
            vectors=embeddings,
            payloads=[{"text": chunk} for chunk in chunks],
        ),
        wait=True,
    )
    logger.info("Successfully upserted chunks to Qdrant.")

def normalize_entity_name(name: str) -> str:
    """
    Normalize entity names before writing to Neo4j.
    Prevents duplicates from casing differences.
    Examples:
        'elon musk'  → 'Elon Musk'
        'SPACEX'     → 'Spacex'  (title case)
        'iPhone 15'  → 'Iphone 15'
    """
    if not name:
        return name
    # Strip extra whitespace
    name = " ".join(name.strip().split())
    # Title case — capitalise first letter of each word
    return name.title()

async def _create_graph_tx(tx, data: List[Dict[str, Any]]):
    """Standard Cypher — no APOC. Normalizes entity names before writing."""
    cypher_query = """
    UNWIND $data AS row
    MERGE (source:Entity {name: row.source})
    MERGE (target:Entity {name: row.target})
    MERGE (source)-[rel:RELATES_TO {type: row.relationship}]->(target)
    ON CREATE SET rel += row.properties, rel.created_at = timestamp()
    ON MATCH  SET rel += row.properties, rel.updated_at = timestamp()
    RETURN count(rel) AS created_rels
    """
    # Normalize entity names before writing
    normalized = []
    for row in data:
        normalized.append({
            **row,
            "source": normalize_entity_name(row["source"]),
            "target": normalize_entity_name(row["target"]),
            "relationship": row["relationship"].upper().replace(" ", "_"),
        })
    result = await tx.run(cypher_query, data=normalized)
    return await result.consume()

async def upsert_graph_to_neo4j(graph: ExtractedGraph):
    if not graph.entities:
        logger.info("No graph entities to upsert.")
        return

    logger.info(f"Upserting {len(graph.entities)} relationships to Neo4j...")
    data_to_load = [entity.model_dump() for entity in graph.entities]
    logger.info(f"Graph data: {data_to_load}")

    async with db_manager.neo4j_driver.session() as session:
        summary = await session.execute_write(_create_graph_tx, data_to_load)
        nodes_created = summary.counters.nodes_created
        rels_created = summary.counters.relationships_created
        logger.info(f"Neo4j write: {nodes_created} nodes, {rels_created} relationships created.")
        if nodes_created == 0 and rels_created == 0:
            logger.warning("0 writes to Neo4j — data may already exist via MERGE.")


async def ingest_text(text: str):
    logger.info("Starting ingestion pipeline...")

    chunks = await chunk_text(text)
    
    # OLD — random ID every time, causes duplicates
    # chunk_ids = [str(uuid.uuid4()) for _ in chunks]

    # NEW — deterministic ID based on content hash, safe to re-ingest
    chunk_ids = [
        str(uuid.UUID(hashlib.md5(chunk.encode()).hexdigest()))
        for chunk in chunks
    ]

    embeddings = await generate_embeddings(chunks)
    await upsert_chunks_to_qdrant(chunk_ids, chunks, embeddings)
    # ... rest unchanged

    graphs = await asyncio.gather(
        *[extract_graph_from_chunk(chunk) for chunk in chunks],
        return_exceptions=True,
    )

    for i, result in enumerate(graphs):
        if isinstance(result, Exception):
            logger.error(f"Graph extraction failed for chunk {i+1}: {result}")
            continue
        if result.entities:
            await upsert_graph_to_neo4j(result)
        else:
            logger.info(f"No entities found in chunk {i+1}.")

    logger.info("Ingestion pipeline completed successfully.")