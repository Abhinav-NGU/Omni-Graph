"""
auth.py — API key authentication dependency.

Protected routes require the header:
    X-API-Key: your-secret-key

Public routes (health, docs, root) are exempt.
"""

import logging
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

from core.config import settings

logger = logging.getLogger(__name__)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    """
    FastAPI dependency — inject into any endpoint to protect it.
    Raises 401 if key is missing, 403 if key is wrong.
    """
    if not api_key:
        logger.warning("Request rejected — missing X-API-Key header.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide it via the X-API-Key header.",
        )
    if api_key != settings.API_KEY:
        logger.warning("Request rejected — invalid API key.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )
    return api_key