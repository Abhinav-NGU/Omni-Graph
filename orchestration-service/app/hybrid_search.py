"""
hybrid_search.py — Phase 8c: Hybrid Search (BM25 + Vector + RRF)

Combines two complementary search strategies:
  - Vector search   → semantic similarity (good for concepts/meaning)
  - BM25 search     → keyword/term matching (good for names/exact terms)

Results are merged using Reciprocal Rank Fusion (RRF):
  score = sum(1 / (rank + k)) for each list the chunk appears in

A chunk appearing in BOTH lists gets a strong boost — this directly
fixes the "Abhinav Bindra scores lower than Elon Musk" problem.
"""

import logging
import math
from typing import List, Dict, Any, Optional

from langchain_ollama.embeddings import OllamaEmbeddings
from qdrant_client import AsyncQdrantClient
from rank_bm25 import BM25Okapi

from core.config import settings
from core.db import db_manager
from ingestion import QDRANT_COLLECTION_NAME

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
VECTOR_TOP_K = 10       # fetch more candidates before RRF merge
BM25_TOP_K = 10
FINAL_TOP_K = 5         # return this many after merging
RRF_K = 60              # RRF constant — higher = less rank difference
MIN_SCORE = 0.0         # after RRF all scores are positive, no threshold needed
# ─────────────────────────────────────────────────────────────────────────────


def _tokenize(text: str) -> List[str]:
    """Simple whitespace + lowercase tokenizer for BM25."""
    return text.lower().split()


def _rrf_score(rank: int, k: int = RRF_K) -> float:
    """Reciprocal Rank Fusion score for a single rank position."""
    return 1.0 / (rank + k)


def _reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    top_k: int = FINAL_TOP_K,
) -> List[Dict[str, Any]]:
    """
    Merge two ranked lists using RRF.

    Each chunk gets score = sum(1/(rank+k)) across all lists it appears in.
    Chunks appearing in both lists get double contribution → bubble to top.
    """
    scores: Dict[str, float] = {}
    chunks_by_id: Dict[str, Dict[str, Any]] = {}

    # Score vector results
    for rank, chunk in enumerate(vector_results):
        cid = chunk["id"]
        scores[cid] = scores.get(cid, 0.0) + _rrf_score(rank)
        chunks_by_id[cid] = {**chunk, "vector_rank": rank + 1}

    # Score BM25 results
    for rank, chunk in enumerate(bm25_results):
        cid = chunk["id"]
        scores[cid] = scores.get(cid, 0.0) + _rrf_score(rank)
        if cid in chunks_by_id:
            chunks_by_id[cid]["bm25_rank"] = rank + 1
        else:
            chunks_by_id[cid] = {**chunk, "bm25_rank": rank + 1}

    # Sort by RRF score descending
    sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)

    results = []
    for cid in sorted_ids[:top_k]:
        chunk = chunks_by_id[cid]
        chunk["score"] = round(scores[cid], 6)
        chunk["rrf_score"] = round(scores[cid], 6)
        # Tag whether it came from both sources
        chunk["in_vector"] = "vector_rank" in chunk
        chunk["in_bm25"] = "bm25_rank" in chunk
        results.append(chunk)

    return results


async def _fetch_all_chunks() -> List[Dict[str, Any]]:
    """
    Fetch all chunks from Qdrant for BM25 indexing.
    BM25 needs the full corpus to compute term frequencies.
    Uses scroll to handle large collections.
    """
    client: AsyncQdrantClient = db_manager.qdrant_client
    all_chunks = []
    offset = None

    try:
        while True:
            results, next_offset = await client.scroll(
                collection_name=QDRANT_COLLECTION_NAME,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in results:
                text = (point.payload or {}).get("text", "")
                if text:
                    all_chunks.append({"id": str(point.id), "text": text})
            if next_offset is None:
                break
            offset = next_offset
    except Exception as e:
        logger.error(f"Failed to fetch chunks for BM25: {e}", exc_info=True)

    return all_chunks


async def _vector_search(question: str) -> List[Dict[str, Any]]:
    """Semantic vector search in Qdrant."""
    try:
        embedder = OllamaEmbeddings(
            model="nomic-embed-text",
            base_url=settings.OLLAMA_BASE_URL,
        )
        query_vector = await embedder.aembed_query(question)
        client: AsyncQdrantClient = db_manager.qdrant_client

        response = await client.query_points(
            collection_name=QDRANT_COLLECTION_NAME,
            query=query_vector,
            limit=VECTOR_TOP_K,
            with_payload=True,
        )

        chunks = []
        for hit in response.points:
            text = (hit.payload or {}).get("text", "")
            if text:
                chunks.append({"id": str(hit.id), "text": text, "score": hit.score})

        logger.info(f"Vector search: {len(chunks)} candidates.")
        return chunks

    except Exception as e:
        logger.error(f"Vector search failed: {e}", exc_info=True)
        return []


def _bm25_search(
    question: str,
    all_chunks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """BM25 keyword search over all chunks."""
    if not all_chunks:
        return []

    try:
        corpus = [_tokenize(c["text"]) for c in all_chunks]
        bm25 = BM25Okapi(corpus)
        query_tokens = _tokenize(question)
        scores = bm25.get_scores(query_tokens)

        # Pair scores with chunks and sort
        scored = sorted(
            zip(scores, all_chunks),
            key=lambda x: x[0],
            reverse=True,
        )

        results = []
        for score, chunk in scored[:BM25_TOP_K]:
            if score > 0:
                results.append({**chunk, "score": float(score)})

        logger.info(f"BM25 search: {len(results)} candidates.")
        return results

    except Exception as e:
        logger.error(f"BM25 search failed: {e}", exc_info=True)
        return []


async def hybrid_search(question: str) -> List[Dict[str, Any]]:
    """
    Main entry point — runs vector + BM25 in parallel then merges with RRF.

    Returns FINAL_TOP_K chunks with rrf_score, in_vector, in_bm25 metadata.
    """
    client: AsyncQdrantClient = db_manager.qdrant_client

    # Check collection exists
    try:
        collections = await client.get_collections()
        names = [c.name for c in collections.collections]
        if QDRANT_COLLECTION_NAME not in names:
            logger.warning(f"Collection '{QDRANT_COLLECTION_NAME}' not found.")
            return []
    except Exception as e:
        logger.error(f"Failed to list collections: {e}")
        return []

    # Fetch all chunks for BM25 (needed to build corpus)
    all_chunks = await _fetch_all_chunks()
    if not all_chunks:
        logger.warning("No chunks in collection — returning empty.")
        return []

    # Run both searches
    vector_results = await _vector_search(question)
    bm25_results = _bm25_search(question, all_chunks)

    # Merge with RRF
    merged = _reciprocal_rank_fusion(vector_results, bm25_results)

    # Log which chunks came from both sources
    both = [c for c in merged if c.get("in_vector") and c.get("in_bm25")]
    logger.info(
        f"Hybrid search: {len(merged)} final chunks "
        f"({len(both)} appeared in both vector + BM25)."
    )

    return merged