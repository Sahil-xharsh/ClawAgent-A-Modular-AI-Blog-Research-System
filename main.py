from __future__ import annotations

import operator
import os
import pathlib
import threading
import time
from typing import Annotated, List, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain.chat_models import init_chat_model
from config.settings import (
    MODEL_NAME,
    MODEL_PROVIDER,
    API_KEY,
    BASE_URL,
    MAX_CONCURRENT_WORKERS,
    OUTPUT_DIR,
)
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from prompts.natural import ORCHESTRATOR_SYSTEM, WORKER_SYSTEM


_worker_semaphore = threading.Semaphore(MAX_CONCURRENT_WORKERS)

class Task(BaseModel):
    id: int
    title: str 

    goal: str = Field(
        ...,
        description = (
            "One sentence: what reader can do or understand after finising this section"
        ),
    )

    bullets: List[str] = Field(
        ...,
        min_length = 3,
        max_length = 5,
        description = "3 - 5 concreye, non-overlapping subpoints to cover in this section",
    )

    target_words: int = Field(
        ...,
        description = "The Target count for this section (120-450)"
    )

    section_type: Literal[
        "intro", "core", "examples", "checklist", "common_mistakes", "conclusion"
    ] = Field(
        ...,
        description = "use 'Common_mistakes' exactly once across the whole plan.",
    
    )

class Plan(BaseModel):
    """The full blog plan retunred by the orchestrator. """

    blog_title: str
    audience: str = Field(..., description = "For whom this blog is written for.")
    tone: str = Field(..., description = "Writing tone e.g. 'practical abd crisp'.")
    tasks: List[Task]

class State(TypedDict):
    topic: str
    plan: Plan
    sections: Annotated[List[tuple], operator.add]
    final: str

# LLM
from config.settings import MODEL_NAME, MODEL_PROVIDER, API_KEY, BASE_URL

llm = init_chat_model(
    model          = MODEL_NAME,
    model_provider = MODEL_PROVIDER,
    api_key        = API_KEY,
    base_url       = BASE_URL,   # None is fine — LangChain ignores it
)


def _invoke_with_retry(messages: list, max_retries: int = 4) -> str:
    """ 
    Call llm.invoke and retry up to max_retries times on RateLimitError.
    Back-off schedule: 5s → 10s → 20s → 40s"""

    delay = 5
    for attempt in range(max_retries + 1):
        try:
            return llm.invoke(messages).content.strip()
        except Exception as e:
            if "429" in str(e) or "RateLimitReached" in str(e):
                if attempt < max_retries:
                    print(f" [retry] 429 hit - waiting {delay}s (arrempt){attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
            else:
                raise

# Node functining

def orchestrator(state: State) -> dict:

    """Generate a structured blog plan from a topic using an LLM.
    with_structured_output(Plan) tells LangChain to enforce the Pydantic schema """
    
    print(f"\n[orchestrator] Planning blog for topic: '{state['topic']}'")

    plan: Plan = llm.with_structured_output(Plan).invoke(
        [
            SystemMessage(content = ORCHESTRATOR_SYSTEM),
            HumanMessage(content = f"Topic: {state['topic']}"),
        ]
    )

    print(f"[orchestrator] Plan ready: '{plan.blog_title}' - {len(plan.tasks)} sections ")
    return {"plan": plan}

def fanout(state: State):

    """
    Not a node - a condional edge fucntion.
    Retunrs one send() per section.

    Each worker payload now includes the full plan (for audiance + tone) """

    print(f"[fanout] Spawning {len(state['plan'].tasks)} parallel workers....")

    return [
        Send(
            "worker",
            {
                "task": task,
                "topic": state["topic"],
                "plan": state["plan"],
            },
        )
        for task in state["plan"].tasks
    ]

def worker(payload: dict) -> dict:
    """
    Section Writer - Receives one Task and writes one Markdown section.
    Returns {"sections": [markdown_string]} — appended to state["sections"]
    by the operator.add reducer."""

    task: Task = payload["task"]
    topic: str = payload["topic"]
    plan: Plan = payload["plan"]

    bullets_text = "\n -" + "\n - ".join(task.bullets)


    with _worker_semaphore:
        print(f" [worker] Writing section {task.id}/{len(plan.tasks)}: '{task.title}'")

        section_md = _invoke_with_retry(
            [
                SystemMessage(content=WORKER_SYSTEM),
                
                # HummanMessage here gives the worker evrything it needs for this section
                # like tone + audience + goal + bullets + target_words

                HumanMessage(
                    content=(
                        f"Blog title:      {plan.blog_title}\n"
                        f"Audience:        {plan.audience}\n"
                        f"Tone:            {plan.tone}\n"
                        f"Overall topic:   {topic}\n\n"
                        f"Section title:   {task.title}\n"
                        f"Section type:    {task.section_type}\n"
                        f"Goal:            {task.goal}\n"
                        f"Target words:    {task.target_words}\n"
                        f"Bullets to cover:{bullets_text}\n"
                    )
                ),
            ]
        )

    return {"sections": [(task.id, section_md)]}


def reducer(state: State) -> dict:
    """
    Sort sections by id and assemble the final Markdown blog post.
    Saves to OUTPUT_DIR/<blog_title>.md (creates the folder if missing)."""

    print("\n[reducer] Assembling final blog post...")

    sorted_sections = [
        md for _, md in sorted(state["sections"], key=lambda x: x[0])
    ]

    title    = state["plan"].blog_title
    body     = "\n\n---\n\n".join(sorted_sections)
    final_md = f"# {title}\n\n{body}\n"

    output_dir = pathlib.Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True) 

    import re
    safe_title = re.sub(r'[\\/:*?"<>|]', "-", title.lower()).replace(" ", "_")
    filename    = safe_title + ".md"
    output_path = output_dir / filename
    output_path.write_text(final_md, encoding="utf-8")

    print(f"[reducer] Saved → {output_path.resolve()}")
    return {"final": final_md}


# The building of the graph
# Starts → orchestrator → fanout → [workers] → reducer → End
def build_graph() -> StateGraph:
    g = StateGraph(State)

    g.add_node("orchestrator", orchestrator)
    g.add_node("worker", worker)
    g.add_node("reducer", reducer)
    
    g.add_edge(START,"orchestrator")
    g.add_conditional_edges("orchestrator", fanout, ["worker"])
    g.add_edge("worker",  "reducer")
    g.add_edge("reducer", END)

    return g.compile()

# The Entry point
def run(topic: str) -> None:
    """Run the blog writer pipeline for a given topic."""
    if not os.environ.get("API_KEY"):
        raise EnvironmentError(
            "API_KEY is not set.\n"
            "Add it to your .env file: API_KEY='your-key-here'"
        )


    app = build_graph()

    print("=" * 60)
    print(f"  Blog Writer — Topic: {topic}")
    print("=" * 60)

    result = app.invoke({"topic": topic, "sections": []})

    print("\n" + "=" * 60)
    print("  DONE — Final blog preview (first 500 chars):")
    print("=" * 60)
    print(result["final"][:500], "...")


if __name__ == "__main__":
    run("Write a blog on Self Attention in Transformers")