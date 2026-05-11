from __future__ import annotations
 
"""
Flow:  START → researcher → orchestrator → fanout → [workers] → reducer → END
 
The researcher:
  1. Builds a focused search query from the topic.
  2. Calls Tavily via tools/search.py.
  3. Summarises the raw results into a tight research brief.
  4. Writes the brief into state["research"] — workers pick it up automatically
     via the research_block in agents/writer.py.
 
If TAVILY_API_KEY is not set the node degrades gracefully:
it logs a warning and writes an empty string so the rest of the pipeline
runs exactly as it did before (writer-only mode)."""

 
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.chat_models import init_chat_model
 
from config.settings import API_KEY, BASE_URL, MODEL_NAME, MODEL_PROVIDER
from prompts.natural import RESEARCHER_SYSTEM
from tools.search import search
from workflow.state import State

# LLM

llm = init_chat_model(
    model          = MODEL_NAME,
    model_provider = MODEL_PROVIDER,
    api_key        = API_KEY,
    base_url       = BASE_URL,   # If None = fine - LangChain will ignore
)

def _clean_query(topic: str) -> str:
    """Strip blog-writing instructions from the topic to get a clean search query.
 
    'Write a blog on Self Attention in Transformers'
    -> 'Self Attention in Transformers'"""

    drop_prefixes = [
        "write a blog on ",
        "write a blog about ",
        "write an article on ",
        "write an article about ",
        "blog post about ",
        "blog on ",
        "blog about ",
        "write about ",
    ]
    cleaned = topic.strip()
    for prefix in drop_prefixes:
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    return cleaned.strip()

 
def researcher(state: State) -> dict:
    """Search the web for the topic and summarise findings into a research brief.
 
    Returns {"research": "<summary string>"} which lands in state["research"].
    Workers read this via the research_block in their prompt (writer.py)."""

    topic        = state["topic"]
    search_query = _clean_query(topic)
 
    print(f"\n[researcher] Searching for: '{search_query}'")
 
    # Step 1 - fetch raw search results using the cleaned query
    try:
        raw_results = search(query=search_query, max_results=5)
    except EnvironmentError as e:
        print(f"[researcher] WARNING: {e}\n[researcher] Skipping — writer-only mode.")
        return {"research": ""}
    except Exception as e:
        print(f"[researcher] ERROR during search: {e}\n[researcher] Skipping.")
        return {"research": ""}
 
    print("[researcher] Search done. Summarising...")
 
    # Step 2 - summarise into a usable research brief
    summary: str = llm.invoke(
        [
            SystemMessage(content=RESEARCHER_SYSTEM),
            HumanMessage(
                content=(
                    f"Topic: {search_query}\n\n"
                    f"Raw search results:\n{raw_results}"
                )
            ),
        ]
    ).content.strip()
 
    word_count = len(summary.split())
 
    # Guard: flag suspiciously short briefs without crashing the pipeline.
    if word_count < 50:
        print(
            f"[researcher] WARNING: Brief is only {word_count} words — "
            "research grounding will be weak. Check RESEARCHER_SYSTEM or your model."
        )
    else:
        print(f"[researcher] Brief ready ({word_count} words).")
 
    return {"research": summary}