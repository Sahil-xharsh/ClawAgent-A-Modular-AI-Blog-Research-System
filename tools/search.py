from __future__ import annotations
import os
from tavily import TavilyClient

def search(query: str, max_results: int = 5) -> str:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "TAVILY_API_KEY is not set.\n"
            "Add it to your .env file: TAVILY_API_KEY='your-key-here'\n"
        )
    
    client = TavilyClient(api_key = api_key)
    results = client.search(query = query, max_results = max_results)

    lines = []

    for i, r in enumerate(results.get("results", []), start=1):
        title   = r.get("title", "No title")
        url     = r.get("url", "")
        content = r.get("content", "").strip()
        lines.append(f"[{i}] {title} — {url}\n    {content}")
 
    return "\n\n".join(lines) if lines else "No results found."

