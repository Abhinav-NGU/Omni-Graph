"""
compressor.py — Phase 8d: Contextual Compression

After retrieval, extracts only the sentences from each chunk that are
directly relevant to the question. Passes a much smaller, focused
context to the final LLM.

Uses llama3.2 for extraction — same model, no new dependencies.
"""

import logging
from typing import List, Dict, Any

from langchain_ollama.chat_models import ChatOllama
from core.config import settings

logger = logging.getLogger(__name__)

# Chunks shorter than this are not worth compressing
MIN_CHARS_TO_COMPRESS = 300

COMPRESSION_PROMPT = """\
You are a precise information extractor.

Given a TEXT CHUNK and a QUESTION, extract ONLY the sentences from the chunk
that are directly relevant to answering the question.

Rules:
- Copy sentences verbatim — do not paraphrase or add information
- If no sentences are relevant, respond with exactly: NO_RELEVANT_CONTENT
- Do not include explanations, headers, or any other text
- Preserve the original wording exactly

QUESTION: {question}

TEXT CHUNK:
{chunk}

Relevant sentences:"""


async def compress_chunk(
    question: str,
    chunk: str,
    llm: ChatOllama,
) -> str:
    """
    Extract only relevant sentences from a chunk for the given question.
    Returns the original chunk if compression fails or chunk is short.
    """
    if len(chunk) < MIN_CHARS_TO_COMPRESS:
        return chunk

    try:
        prompt = COMPRESSION_PROMPT.format(question=question, chunk=chunk)
        response = await llm.ainvoke([("human", prompt)])
        compressed = response.content.strip()

        if not compressed or compressed == "NO_RELEVANT_CONTENT":
            return ""

        # Safety check — if compressed is longer than original something went wrong
        if len(compressed) >= len(chunk):
            logger.debug("Compression produced output longer than input — using original.")
            return chunk

        logger.debug(
            f"Compressed chunk: {len(chunk)} → {len(compressed)} chars "
            f"({round((1 - len(compressed)/len(chunk)) * 100)}% reduction)"
        )
        return compressed

    except Exception as e:
        logger.warning(f"Compression failed for chunk — using original. Error: {e}")
        return chunk


async def compress_chunks(
    question: str,
    chunks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Compress all retrieved chunks in sequence.
    Filters out chunks that have no relevant content.

    Returns compressed chunks with original metadata preserved.
    """
    if not chunks:
        return []

    llm = ChatOllama(
        model="llama3.2",
        base_url=settings.OLLAMA_BASE_URL,
        temperature=0.0,   # deterministic extraction
    )

    logger.info(f"Compressing {len(chunks)} chunks for question: '{question[:60]}...'")

    total_before = sum(len(c["text"]) for c in chunks)
    compressed_chunks = []

    for i, chunk in enumerate(chunks):
        compressed_text = await compress_chunk(question, chunk["text"], llm)

        if compressed_text:
            compressed_chunks.append({
                **chunk,
                "text": compressed_text,
                "original_text": chunk["text"],      # keep original for debug
                "compressed": compressed_text != chunk["text"],
            })
        else:
            logger.debug(f"Chunk {i+1} had no relevant content — dropped.")

    total_after = sum(len(c["text"]) for c in compressed_chunks)
    dropped = len(chunks) - len(compressed_chunks)

    logger.info(
        f"Compression complete: {len(chunks)} → {len(compressed_chunks)} chunks "
        f"({dropped} dropped), "
        f"{total_before} → {total_after} chars "
        f"({round((1 - total_after/max(total_before, 1)) * 100)}% reduction)."
    )

    return compressed_chunks