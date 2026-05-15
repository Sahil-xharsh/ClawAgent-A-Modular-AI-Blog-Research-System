from __future__ import annotations
 
"""
Pipeline flow:
    START → researcher → orchestrator → fanout → [workers]
                                                      ↓
                                                  reviewer  ──(pass)──→ reducer → END
                                                      ↑
                                                  worker   ←─(retry)──┘
 
Reviewer logic (per section):
    1. Word-count pre-filter  — cheap, no API call.
       Fail if outside ±15% of task.target_words → immediate retry.
    2. LLM-as-judge           — scores 1–10 on bullet coverage, technical
       depth, and Markdown correctness. Score < 7 → one retry allowed.
    Max one rewrite per section to cap cost."""
 
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
 
from agents.researcher import researcher
from agents.writer import fanout, orchestrator, reducer, worker, _semaphore, _invoke_with_retry
from config.settings import API_KEY, MODEL_NAME, MODEL_PROVIDER, BASE_URL
from prompts.loader import prompts
from utils.logger import log
from workflow.state import Plan, State, Task
 

# LLM
 
_reviewer_llm = init_chat_model(
    model          = MODEL_NAME,
    model_provider = MODEL_PROVIDER,
    api_key        = API_KEY,
    base_url       = BASE_URL,
)
 
_SCORE_THRESHOLD = 7     # minimum acceptable score out of 10
_WORD_TOLERANCE  = 0.15  # ±15% of target_words triggers word-count fail
 
 
def _word_count_ok(text: str, target: int) -> bool:
    count = len(text.split())
    low   = target * (1 - _WORD_TOLERANCE)
    high  = target * (1 + _WORD_TOLERANCE)
    return low <= count <= high
 
 
def _llm_score(task: Task, section_md: str) -> int:
    """Ask the reviewer LLM to score one section. Returns int 1-10."""
    bullets_text = "\n- " + "\n- ".join(task.bullets)
    prompt = (
        f"Section title:   {task.title}\n"
        f"Section type:    {task.section_type}\n"
        f"Goal:            {task.goal}\n"
        f"Bullets to cover:{bullets_text}\n"
        f"Target words:    {task.target_words}\n\n"
        f"--- SECTION CONTENT ---\n{section_md}"
    )
    with _semaphore:
        raw = _invoke_with_retry(
            [
                SystemMessage(content=prompts.reviewer),
                HumanMessage(content=prompt),
            ]
        )
    try:
        return max(1, min(10, int(raw.strip())))
    except ValueError:
        log.warning("Reviewer returned non-integer: '{raw}' — defaulting to 5", raw=raw)
        return 5
 
 
def _rewrite(
    task: Task,
    plan: Plan,
    topic: str,
    research: str,
    original_md: str,
    reason: str,
) -> str:
    """Rewrite a rejected section, injecting the original draft so the model
    knows what to fix rather than starting from scratch."""
 
    bullets_text   = "\n - " + "\n - ".join(task.bullets)
    research_block = (
        f"\nResearch context (use as factual grounding):\n{research}\n"
        if research else ""
    )
    feedback_block = (
        f"\n--- PREVIOUS DRAFT (rejected: {reason}) ---\n"
        f"{original_md}\n"
        f"--- END PREVIOUS DRAFT ---\n"
        f"Rewrite the section above. Fix the issue ({reason}) while keeping "
        "what was already good.\n"
    )
 
    with _semaphore:
        log.info("Rewriting section {id}: '{title}'", id=task.id, title=task.title)
        rewritten = _invoke_with_retry(
            [
                SystemMessage(content=prompts.worker),
                HumanMessage(
                    content=(
                        f"Blog title:      {plan.blog_title}\n"
                        f"Audience:        {plan.audience}\n"
                        f"Tone:            {plan.tone}\n"
                        f"Overall topic:   {topic}\n"
                        f"{research_block}"
                        f"Section title:   {task.title}\n"
                        f"Section type:    {task.section_type}\n"
                        f"Goal:            {task.goal}\n"
                        f"Target words:    {task.target_words}\n"
                        f"Bullets to cover:{bullets_text}\n"
                        f"{feedback_block}"
                    )
                ),
            ]
        )
 
    return rewritten
 
 
# The Reviewer node
def reviewer(payload: dict) -> dict:
    """Evaluate one section. Rewrite it once if quality is below the bar."""
 
    task:     Task = payload["task"]
    plan:     Plan = payload["plan"]
    topic:    str  = payload["topic"]
    research: str  = payload.get("research", "")
 
    section_id, section_md = payload["sections"][0]
 
    log.info(
        "Reviewing section {id}/{total}: '{title}'",
        id=task.id, total=len(plan.tasks), title=task.title,
    )
 
    # Pass 1: word-count pre-filter
    if not _word_count_ok(section_md, task.target_words):
        actual = len(section_md.split())
        log.warning(
            "Section {id} word count {actual} outside target {target} ±15% — rewriting",
            id=task.id, actual=actual, target=task.target_words,
        )
        section_md = _rewrite(task, plan, topic, research, section_md, reason="word count")
 
    # Pass 2: LLM quality score
    score = _llm_score(task, section_md)
    log.info("Section {id} scored {score}/10", id=task.id, score=score)
 
    if score < _SCORE_THRESHOLD:
        log.warning(
            "Section {id} scored {score} < {threshold} — rewriting (last chance)",
            id=task.id, score=score, threshold=_SCORE_THRESHOLD,
        )
        section_md = _rewrite(
            task, plan, topic, research, section_md,
            reason=f"quality score {score}/10",
        )
 
    log.info("Section {id} accepted.", id=task.id)
    return {"sections": [(section_id, section_md)]}
 
 

# Reviewer fanout
 
def reviewer_fanout(state: State):
    """Route each completed section to the reviewer in parallel."""
 
    plan        = state["plan"]
    topic       = state["topic"]
    research    = state.get("research", "")
    tasks_by_id = {t.id: t for t in plan.tasks}
 
    return [
        Send(
            "reviewer",
            {
                "task":     tasks_by_id[section_id],
                "plan":     plan,
                "topic":    topic,
                "research": research,
                "sections": [(section_id, section_md)],
            },
        )
        for section_id, section_md in state["sections"]
    ]
 
 
# the Graph
 
def build_graph() -> StateGraph:
    g = StateGraph(State)
 
    g.add_node("researcher",   researcher)
    g.add_node("orchestrator", orchestrator)
    g.add_node("worker",       worker)
    g.add_node("reviewer",     reviewer)
    g.add_node("reducer",      reducer)
 
    g.add_edge(START, "researcher")
    g.add_edge("researcher", "orchestrator")
    g.add_conditional_edges("orchestrator", fanout,          ["worker"])
    g.add_conditional_edges("worker",       reviewer_fanout, ["reviewer"])
    g.add_edge("reviewer", "reducer")
    g.add_edge("reducer",  END)
 
    return g.compile()
 
 
# The  Entry point
def run(topic: str) -> None:
    if not API_KEY:
        raise EnvironmentError(
            "API_KEY is not set.\n"
            "Add it to your .env file alongside MODEL_NAME, MODEL_PROVIDER, BASE_URL."
        )
 
    app = build_graph()
 
    log.info("Blog Writer — Topic: {topic}", topic=topic)
    result = app.invoke({"topic": topic, "sections": [], "research": ""})
    log.info("DONE — Final blog preview (first 500 chars):")
    log.info("\n" + result["final"][:500] + " ...")