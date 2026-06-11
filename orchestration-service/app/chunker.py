"""
chunker.py — Phase 8b: Semantic Chunking

Replaces fixed-size character chunking with topic-aware splitting.

Algorithm:
  1. Split text into sentences
  2. Embed each sentence using nomic-embed-text
  3. Compute cosine similarity between adjacent sentences
  4. Split where similarity drops below threshold (topic boundary)
  5. Merge small chunks to avoid tiny fragments

This produces chunks that contain complete thoughts rather than
arbitrary character-count slices.
"""

import logging
import re
from typing import List
import math

from langchain_ollama.embeddings import OllamaEmbeddings
from core.config import settings

logger = logging.getLogger(__name__)

# ── Tuneable constants ────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD = 0.75   # below this = topic boundary
MIN_CHUNK_CHARS = 200          # merge chunks smaller than this
MAX_CHUNK_CHARS = 1500         # hard cap — split if chunk exceeds this
SENTENCE_BATCH_SIZE = 32       # embed sentences in batches
# ─────────────────────────────────────────────────────────────────────────────


def _split_into_sentences(text: str) -> List[str]:
    """
    Split text into sentences using regex.
    Uses a simple but reliable approach compatible with Python's re module.
    """
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text.strip())

    # Replace known abbreviations temporarily to avoid false splits
    abbrev_map = {
        "Mr.": "Mr_DOT_", "Mrs.": "Mrs_DOT_", "Dr.": "Dr_DOT_",
        "Prof.": "Prof_DOT_", "Sr.": "Sr_DOT_", "Jr.": "Jr_DOT_",
        "vs.": "vs_DOT_", "etc.": "etc_DOT_", "i.e.": "ie_DOT_",
        "e.g.": "eg_DOT_", "Fig.": "Fig_DOT_", "No.": "No_DOT_",
        "Vol.": "Vol_DOT_",
    }
    for abbrev, placeholder in abbrev_map.items():
        text = text.replace(abbrev, placeholder)

    # Split on sentence-ending punctuation followed by space + capital
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)

    # Restore abbreviations
    restored = []
    for s in sentences:
        for abbrev, placeholder in abbrev_map.items():
            s = s.replace(placeholder, abbrev)
        s = s.strip()
        if s and len(s) > 10:
            restored.append(s)

    return restored

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def _embed_sentences(sentences: List[str]) -> List[List[float]]:
    """Embed all sentences in batches."""
    embedder = OllamaEmbeddings(
        model="nomic-embed-text",
        base_url=settings.OLLAMA_BASE_URL,
    )
    all_embeddings = []
    for i in range(0, len(sentences), SENTENCE_BATCH_SIZE):
        batch = sentences[i:i + SENTENCE_BATCH_SIZE]
        embeddings = await embedder.aembed_documents(batch)
        all_embeddings.extend(embeddings)
    return all_embeddings


def _find_split_points(
    similarities: List[float],
    threshold: float = SIMILARITY_THRESHOLD,
) -> List[int]:
    """
    Find indices where topic changes.
    A split point means: start a new chunk after sentence[i].
    """
    split_points = []
    for i, sim in enumerate(similarities):
        if sim < threshold:
            split_points.append(i + 1)  # split before next sentence
    return split_points


def _group_sentences(
    sentences: List[str],
    split_points: List[int],
) -> List[str]:
    """
    Group sentences into chunks based on split points.
    Merges chunks that are too small, splits chunks that are too large.
    """
    if not sentences:
        return []

    # Build initial chunks
    chunks = []
    current_start = 0
    for split_point in split_points:
        chunk_sentences = sentences[current_start:split_point]
        if chunk_sentences:
            chunks.append(" ".join(chunk_sentences))
        current_start = split_point
    # Add final chunk
    if current_start < len(sentences):
        chunks.append(" ".join(sentences[current_start:]))

    # Merge chunks that are too small into the previous chunk
    merged = []
    for chunk in chunks:
        if merged and len(chunk) < MIN_CHUNK_CHARS:
            merged[-1] = merged[-1] + " " + chunk
        else:
            merged.append(chunk)

    # Split chunks that exceed max size
    final = []
    for chunk in merged:
        if len(chunk) <= MAX_CHUNK_CHARS:
            final.append(chunk)
        else:
            # Hard split on sentence boundaries within the chunk
            words = chunk.split()
            current = []
            current_len = 0
            for word in words:
                current.append(word)
                current_len += len(word) + 1
                if current_len >= MAX_CHUNK_CHARS:
                    final.append(" ".join(current))
                    current = []
                    current_len = 0
            if current:
                final.append(" ".join(current))

    return [c.strip() for c in final if c.strip()]


async def semantic_chunk(text: str) -> List[str]:
    """
    Main entry point — split text into semantically coherent chunks.

    Falls back to simple paragraph splitting if text is too short
    to benefit from semantic analysis.
    """
    # For very short texts just return as single chunk
    if len(text) < MIN_CHUNK_CHARS * 2:
        logger.info("Text too short for semantic chunking — returning as single chunk.")
        return [text.strip()]

    logger.info(f"Semantic chunking: {len(text)} characters...")

    sentences = _split_into_sentences(text)
    logger.info(f"Split into {len(sentences)} sentences.")

    if len(sentences) <= 2:
        return [text.strip()]

    # Embed all sentences
    embeddings = await _embed_sentences(sentences)

    # Compute similarity between adjacent sentences
    similarities = []
    for i in range(len(embeddings) - 1):
        sim = _cosine_similarity(embeddings[i], embeddings[i + 1])
        similarities.append(sim)

    # Log similarity stats for debugging
    if similarities:
        avg_sim = sum(similarities) / len(similarities)
        min_sim = min(similarities)
        logger.info(
            f"Sentence similarities — avg: {avg_sim:.3f}, "
            f"min: {min_sim:.3f}, threshold: {SIMILARITY_THRESHOLD}"
        )

    # Find topic boundaries
    split_points = _find_split_points(similarities)
    logger.info(f"Found {len(split_points)} topic boundaries.")

    # Group into final chunks
    chunks = _group_sentences(sentences, split_points)
    logger.info(
        f"Semantic chunking complete: {len(chunks)} chunks "
        f"(avg {sum(len(c) for c in chunks) // max(len(chunks), 1)} chars each)."
    )

    return chunks