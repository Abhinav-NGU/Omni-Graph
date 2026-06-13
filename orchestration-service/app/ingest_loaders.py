"""
ingest_loaders.py — PDF and URL content extractors for Phase 4.

Provides two loaders:
  - load_pdf(file_bytes)  → extracts text from a PDF file
  - load_url(url)         → fetches and extracts clean text from a webpage
"""

import logging
import io
import asyncio
from typing import Optional

import httpx
from pypdf import PdfReader
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


async def load_pdf(file_bytes: bytes) -> str:
    """
    Extract all text from a PDF given its raw bytes.
    Returns the full text as a single string.
    """
    logger.info("Extracting text from PDF...")

    def _extract_sync():
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text and text.strip():
                pages.append(text.strip())
            else:
                logger.warning(f"Page {i+1} returned no text (may be image-based).")

        if not pages:
            raise ValueError("PDF contains no extractable text. It may be scanned/image-based.")

        return "\n\n".join(pages), len(reader.pages)

    try:
        loop = asyncio.get_running_loop()
        # Offload the CPU-bound parsing to a background thread to avoid blocking the event loop
        full_text, num_pages = await loop.run_in_executor(None, _extract_sync)
        logger.info(f"PDF extracted: {num_pages} pages, {len(full_text)} characters.")
        return full_text

    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        raise


async def load_url(url: str) -> str:
    """
    Fetch a webpage and extract its main text content.
    Strips scripts, styles, nav, and other non-content elements.
    """
    logger.info(f"Fetching URL: {url}")
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "OmniGraph-Ingestion-Bot/1.0"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            raise ValueError(
                f"URL returned unsupported content type: {content_type}. "
                "Only HTML and plain text pages are supported."
            )

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "header", "footer",
                          "aside", "form", "noscript", "iframe"]):
            tag.decompose()

        # Extract clean text
        text = soup.get_text(separator="\n", strip=True)

        # Collapse excessive blank lines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        clean_text = "\n".join(lines)

        if not clean_text:
            raise ValueError("URL returned an empty page after text extraction.")

        logger.info(f"URL extracted: {len(clean_text)} characters from {url}")
        return clean_text

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching URL {url}: {e.response.status_code}")
        raise
    except httpx.RequestError as e:
        logger.error(f"Network error fetching URL {url}: {e}")
        raise