"""Firecrawl web search with optional API-key authentication.

When FIRECRAWL_API_KEY is unset, requests use Firecrawl's keyless API access.
"""
import os

import requests


SEARCH_URL = "https://api.firecrawl.dev/v2/search"


def search_web(query: str, limit: int = 3, timeout: int = 60):
    """Search the web and return normalized results with extracted page text.

    Raises RuntimeError on provider/API errors so the caller can fall back to
    another configured search source.
    """
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "query": query,
        "limit": max(1, min(int(limit), 10)),
        "sources": ["web"],
        "scrapeOptions": {"formats": [{"type": "markdown"}]},
    }
    try:
        response = requests.post(SEARCH_URL, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"Firecrawl request failed: {exc}") from exc

    if not response.ok:
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("error") or body.get("message") or "")[:240]
        except (ValueError, AttributeError):
            detail = (response.text or "")[:240]
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Firecrawl returned HTTP {response.status_code}{suffix}")

    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError("Firecrawl returned invalid JSON") from exc

    if not isinstance(body, dict) or body.get("success") is False:
        detail = body.get("error", "unsuccessful response") if isinstance(body, dict) else "invalid response"
        raise RuntimeError(f"Firecrawl search failed: {str(detail)[:240]}")

    data = body.get("data", {})
    results = data.get("web", []) if isinstance(data, dict) else []
    if not isinstance(results, list):
        return []

    normalized = []
    for item in results:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "title": str(item.get("title") or "Search result"),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("description") or ""),
            "markdown": str(item.get("markdown") or ""),
        })
    return normalized
