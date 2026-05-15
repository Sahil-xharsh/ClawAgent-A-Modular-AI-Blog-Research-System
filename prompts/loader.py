from __future__ import annotations
 
"""
prompts/loader.py
 
Loads toon.json and exposes all system prompts as a single `prompts` object.
TOON is the only mode — natural.py stays as a reference but is not loaded at runtime.
 
Usage in any agent or pipeline module:
    from prompts.loader import prompts
    SystemMessage(content=prompts.orchestrator)
    SystemMessage(content=prompts.researcher)
    SystemMessage(content=prompts.worker)
    SystemMessage(content=prompts.reviewer)"""
 
import json
import pathlib
from dataclasses import dataclass
 
from utils.logger import log
 
_TOON_PATH = pathlib.Path(__file__).parent / "toon.json"
 
 
@dataclass(frozen=True)
class Prompts:
    orchestrator: str
    researcher:   str
    worker:       str
    reviewer:     str
 
 
def _load() -> Prompts:
    if not _TOON_PATH.exists():
        raise FileNotFoundError(
            f"toon.json not found at {_TOON_PATH}. "
            "Make sure prompts/toon.json exists in your project."
        )
 
    with open(_TOON_PATH, encoding="utf-8") as f:
        raw = json.load(f)
 
    log.info("Prompts loaded from toon.json")
 
    return Prompts(
        orchestrator = json.dumps(raw["orchestrator"], indent=2),
        researcher   = json.dumps(raw["researcher"],   indent=2),
        worker       = json.dumps(raw["worker"],       indent=2),
        reviewer     = json.dumps(raw["reviewer"],     indent=2),
    )
 
 
# Module-level singleton
prompts: Prompts = _load()