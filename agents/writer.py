from __future__ import annotations

"""
All writer-side LangGraph nodes:
  - orchestrator  → plans the blog (structured output → Plan)
  - fanout        → conditional edge, spawns one Send() per section
  - worker        → writes one Markdown section (throttled by semaphore)
  - reducer       → assembles + saves the final .md file
 
Import into workflow/pipeline.py to wire into the graph."""

import pathlib
import re
import threading
import time
 
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.chat_models import init_chat_model
from langgraph.types import Send
 
from config.settings import (
    API_KEY,
    BASE_URL,
    MAX_CONCURRENT_WORKERS,
    MODEL_NAME,
    MODEL_PROVIDER,
    OUTPUT_DIR,
)
from prompts.natural import ORCHESTRATOR_SYSTEM, WORKER_SYSTEM
from workflow.state import Plan, State, Task

# ── LLM ─────────────────────────────────────────────────────────────────────
 
llm = init_chat_model(
    model          = MODEL_NAME,
    model_provider = MODEL_PROVIDER,
    api_key        = API_KEY,
    base_url       = BASE_URL,
)

_semaphore = threading.Semaphore(MAX_CONCURRENT_WORKERS)

def _invoke_with_retry(messages: list, max_retries: int = 4) -> str:
    """Back-off schedule: 5s → 10s → 20s → 40s.
    Safety net for the rare 429 that slips past the semaphore."""

    delay = 5
    for attempt in range(max_retries + 1):
        try:
            return llm.invoke(messages).content.strip()
        except Exception as exc:
            if "429" in str(exc) or "RateLimitReached" in str(exc):
                if attempt < max_retries:
                    print(
                        f"   [retry] 429 hit — waiting {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
            else:
                raise

# Node functining

def orchestrator(state: State) -> dict:
    """Plan the blog: topic → structured Plan (5–7 sections).
 
    with_structured_output(Plan) enforces the Pydantic schema - always returns
    a valid Plan or raises, never silent garbage."""

    print(f"\n[orchestrator] Planning blog for: '{state['topic']}'")
 
    plan: Plan = llm.with_structured_output(Plan).invoke(
        [
            SystemMessage(content=ORCHESTRATOR_SYSTEM),
            HumanMessage(content=f"Topic: {state['topic']}"),
        ]
    )
 
    print(f"[orchestrator] Plan ready: '{plan.blog_title}' — {len(plan.tasks)} sections")
    return {"plan": plan}
 
 
def fanout(state: State):
    """Not a node - a condional edge fucntion.
    Retunrs one send() per section.
    
    Each worker payload now includes the full plan (for audiance + tone) """
    
    print(
        f"[fanout] Spawning {len(state['plan'].tasks)} workers "
        f"(max {MAX_CONCURRENT_WORKERS} concurrent)..."
    )
    return [
        Send(
            "worker",
            {
                "task":     task,
                "topic":    state["topic"],
                "plan":     state["plan"],
                "research": state.get("research", ""),   # empty string until Step 3
            },
        )
        for task in state["plan"].tasks
    ]

def worker(payload: dict) -> dict:
    """Write one Markdown section from a Task.
 
    Acquires _semaphore before calling the API - releases automatically on exit.
    Returns {"sections": [(id, markdown)]} appended by operator.add."""

    task:     Task   = payload["task"]
    topic:    str    = payload["topic"]
    plan:     Plan   = payload["plan"]
    research: str    = payload.get("research", "")
 
    bullets_text = "\n - " + "\n - ".join(task.bullets)
 
    research_block = (
        f"\nResearch context (use as factual grounding):\n{research}\n"
        if research else ""
    )
 
    with _semaphore:
        print(f" [worker] Writing section {task.id}/{len(plan.tasks)}: '{task.title}'")
 
        section_md = _invoke_with_retry(
            [
                SystemMessage(content=WORKER_SYSTEM),
                HumanMessage(
                    content=(
                        f"Blog title:      {plan.blog_title}\n"
                        f"Audience:        {plan.audience}\n"
                        f"Tone:            {plan.tone}\n"
                        f"Overall topic:   {topic}\n"
                        f"{research_block}\n"
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
    """Sort sections by id, assemble final Markdown, save to outputs/.
 
    Strips Windows-illegal filename characters so the file saves correctly
    on all platforms."""
    
    print("\n[reducer] Assembling final blog post...")
 
    sorted_sections = [
        md for _, md in sorted(state["sections"], key=lambda x: x[0])
    ]
 
    title    = state["plan"].blog_title
    body     = "\n\n---\n\n".join(sorted_sections)
    final_md = f"# {title}\n\n{body}\n"
 
    output_dir = pathlib.Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
 
    # Strip characters illegal in Windows filenames: \ / : * ? " < > |
    safe_title  = re.sub(r'[\\/:*?"<>|]', "-", title.lower()).replace(" ", "_")
    output_path = output_dir / (safe_title + ".md")
    output_path.write_text(final_md, encoding="utf-8")
 
    print(f"[reducer] Saved → {output_path.resolve()}")
    return {"final": final_md}
 