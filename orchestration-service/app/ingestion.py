import asyncio
import logging
import uuid
from typing import List, Dict, Any

from fastapi import HTTPException
from langchain_ollama.chat_models import ChatOllama
from langchain_ollama.embeddings import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import models
from qdrant_client import AsyncQdrantClient
from core.config import settings
from core.db import db_manager
from graph import ExtractedGraph

logger = logging.getLogger(__name__)

QDRANT_COLLECTION_NAME = "omnigraph_chunks"


def _get_embedding_model() -> OllamaEmbeddings:
    return OllamaEmbeddings(model="nomic-embed-text", base_url=settings.OLLAMA_BASE_URL)


def _get_graph_extraction_llm() -> ChatOllama:
    return ChatOllama(
        model="llama3",
        base_url=settings.OLLAMA_BASE_URL,
        temperature=0.0,
    ).with_structured_output(ExtractedGraph)


async def chunk_text(text: str) -> List[str]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
    )
    return text_splitter.split_text(text)


async def generate_embeddings(chunks: List[str]) -> List[List[float]]:
    logger.info(f"Generating embeddings for {len(chunks)} chunks...")
    embedder = _get_embedding_model()
    return await embedder.aembed_documents(chunks)


async def extract_graph_from_chunk(chunk: str) -> ExtractedGraph:
    logger.info("Extracting graph from chunk...")
    llm = _get_graph_extraction_llm()

    prompt = f"""
    You are an expert data analyst. Your task is to extract a knowledge graph from the following text.
    Identify entities (people, organizations, locations, concepts) and the relationships between them.
    Provide the output as a list of JSON objects, where each object represents a single relationship.
    Each object must have a 'source', 'target', 'relationship', and an optional 'properties' field.
    The 'relationship' should be a concise, uppercase verb phrase (e.g., 'HIRED', 'LOCATED_IN').

    Example Input: "Apple Inc., based in Cupertino, announced the new iPhone 15 in September 2023."
    Example Output:
    {{
        "entities": [
            {{
                "source": "Apple Inc.",
                "target": "Cupertino",
                "relationship": "BASED_IN",
                "properties": {{}}
            }},
            {{
                "source": "Apple Inc.",
                "target": "iPhone 15",
                "relationship": "ANNOUNCED",
                "properties": {{
                    "date": "September 2023"
                }}
            }}
        ]
    }}

    Now, analyze the following text:

    ---
    {chunk}
    ---
    """
    return await llm.ainvoke(prompt)


async def _ensure_qdrant_collection(qdrant_client: AsyncQdrantClient, embedding_size: int):
    try:
        await qdrant_client.get_collection(collection_name=QDRANT_COLLECTION_NAME)
        logger.info(f"Qdrant collection '{QDRANT_COLLECTION_NAME}' already exists.")
    except Exception:
        logger.info(f"Qdrant collection '{QDRANT_COLLECTION_NAME}' not found. Creating it.")
        await qdrant_client.create_collection(
            collection_name=QDRANT_COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=embedding_size,
                distance=models.Distance.COSINE
            ),
        )


async def upsert_chunks_to_qdrant(
    chunk_ids: List[str], chunks: List[str], embeddings: List[List[float]]
):
    logger.info(f"Upserting {len(chunks)} chunks to Qdrant...")
    if not embeddings:
        logger.warning("No embeddings provided; skipping upsert to Qdrant.")
        return

    qdrant_client: AsyncQdrantClient = db_manager.qdrant_client  # Fix 4: must be AsyncQdrantClient
    await _ensure_qdrant_collection(qdrant_client, embedding_size=len(embeddings[0]))

    await qdrant_client.upsert(
        collection_name=QDRANT_COLLECTION_NAME,
        points=models.Batch(
            ids=chunk_ids,
            vectors=embeddings,
            payloads=[{"text": chunk} for chunk in chunks],
        ),
        wait=True,
    )
    logger.info("Successfully upserted chunks to Qdrant.")


async def _create_graph_tx(tx, data: List[Dict[str, Any]]):
    """
    Creates graph entities using standard Cypher only — no APOC required.
    The extracted relationship type is stored as a 'type' property on a
    generic RELATES_TO edge, which is a standard graph modelling pattern
    when relationship types are dynamic/unknown at schema time.
    """
    cypher_query = """
    UNWIND $data AS row
    MERGE (source:Entity {name: row.source})
    MERGE (target:Entity {name: row.target})
    MERGE (source)-[rel:RELATES_TO {type: row.relationship}]->(target)
    ON CREATE SET rel += row.properties, rel.created_at = timestamp()
    ON MATCH  SET rel += row.properties, rel.updated_at = timestamp()
    RETURN count(rel) AS created_rels
    """
    result = await tx.run(cypher_query, data=data)
    return await result.consume()


async def upsert_graph_to_neo4j(graph: ExtractedGraph):
    if not graph.entities:
        logger.info("No graph entities to upsert into Neo4j.")
        return

    logger.info(f"Upserting {len(graph.entities)} relationships to Neo4j...")
    data_to_load = [entity.model_dump() for entity in graph.entities]

    # Fix 6: neo4j_driver must be AsyncDriver for async session/execute_write
    async with db_manager.neo4j_driver.session() as session:
        summary = await session.execute_write(_create_graph_tx, data_to_load)
        logger.info(
            f"Neo4j write complete: "
            f"{summary.counters.relationships_created} relationships created."
        )


async def ingest_text(text: str):
    logger.info("Starting ingestion pipeline...")

    chunks = await chunk_text(text)
    chunk_ids = [str(uuid.uuid4()) for _ in chunks]

    embeddings = await generate_embeddings(chunks)
    await upsert_chunks_to_qdrant(chunk_ids, chunks, embeddings)

    # Fix 7: Run graph extraction concurrently instead of sequentially
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
            logger.info(f"No graph entities found in chunk {i+1}.")

    logger.info("Ingestion pipeline completed successfully.")