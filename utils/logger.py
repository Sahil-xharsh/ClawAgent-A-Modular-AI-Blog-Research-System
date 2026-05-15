from __future__ import annotations
 
"""
Single import, structured logs - replaces all print() statements.
 
Usage (in any module):
    from utils.logger import log
 
    log.info("Planning blog for: '{topic}'", topic=state["topic"])
    log.warning("Brief is only {n} words", n=word_count)
    log.error("API call failed: {err}", err=exc)"""
 
import os
import sys
 
from loguru import logger
 
_LEVEL  = os.environ.get("LOG_LEVEL", "INFO").upper()
_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan> — "
    "<level>{message}</level>"
)
 
logger.remove()
logger.add(
    sys.stderr,
    level  = _LEVEL,
    format = _FORMAT,
    colorize = True,
)
 
_LOG_FILE = os.environ.get("LOG_FILE", "")
if _LOG_FILE:
    logger.add(
        _LOG_FILE,
        level    = _LEVEL,
        format   = _FORMAT,
        colorize = False,
        rotation = "10 MB",
        retention = "7 days",
    )

log = logger