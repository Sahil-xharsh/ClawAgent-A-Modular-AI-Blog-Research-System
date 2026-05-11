from __future__ import annotations
from langgraph.graph import END, START, StateGraph
from agents.researcher import researcher
from agents.writer import fanout, orchestrator, reducer, worker
from config.settings import API_KEY
from workflow.state import State

def build_graph() -> StateGraph:
    g = StateGraph(State)
 
    # ── Nodes ────────────────────────────────────────────────────────────────
    g.add_node("researcher",   researcher)
    g.add_node("orchestrator", orchestrator)
    g.add_node("worker",       worker)
    g.add_node("reducer",      reducer)
 
    # ── Edges ────────────────────────────────────────────────────────────────
    g.add_edge(START, "researcher")                              # research first
    g.add_edge("researcher", "orchestrator")                     # then plan
    g.add_conditional_edges("orchestrator", fanout, ["worker"])  # parallel write
    g.add_edge("worker",  "reducer")                             # assemble
    g.add_edge("reducer", END)
 
    return g.compile()
 
 
def run(topic: str) -> None:
    if not API_KEY:
        raise EnvironmentError(
            "API_KEY is not set.\n"
            "Add it to your .env file alongside MODEL_NAME, MODEL_PROVIDER, BASE_URL."
        )

    app = build_graph()
 
    print("=" * 60)
    print(f"  Blog Writer — Topic: {topic}")
    print("=" * 60)
 
    result = app.invoke({"topic": topic, "sections": [], "research": ""})
 
    print("\n" + "=" * 60)
    print("  DONE — Final blog preview (first 500 chars):")
    print("=" * 60)
    print(result["final"][:500], "...")
 
 
if __name__ == "__main__":
    run("Write a blog on Self Attention in Transformers")