from __future__ import annotations
 
import operator
from typing import Annotated, List, Literal
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


# orchestrator output / worker input

class Task(BaseModel):
    """A section of the blog — produced by the orchestrator, consumed by workers."""
 
    id: int
    title: str
 
    goal: str = Field(
        ...,
        description="One sentence: what the reader can do/understand after this section.",
    )

    bullets: List[str] = Field(
        ...,
        min_length=3,
        max_length=5,
        description="3–5 concrete non-overlapping subpoints to cover.",
    )

    target_words: int = Field(
        ...,
        description="Target word count for this section (120–450).",
    )


    section_type: Literal[
        "intro", "core", "examples", "checklist", "common_mistakes", "conclusion"
    ] = Field(
        ...,
        description="Use 'common_mistakes' exactly once across the whole plan.",
    )

class Plan(BaseModel):
    """Full blog plan returned by the orchestrator."""
 
    blog_title: str
    audience: str = Field(..., description="Who this blog is written for.")
    tone: str     = Field(..., description="Writing tone, e.g. 'practical and crisp'.")
    tasks: List[Task]

 
class State(TypedDict):
    topic:    str
    research: str
    plan:     Plan
    sections: Annotated[List[tuple], operator.add]
    final:    str 