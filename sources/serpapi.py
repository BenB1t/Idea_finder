"""
SerpAPI source module — structured Google data (search, PAA, trends, KG).
Free tier: 100 searches/month.
"""
import os
import requests
from typing import List, Dict, Optional

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
BASE = "https://serpapi.com/search.json"


def _call(params, timeout=30):
    if not SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY not set. Get one at https://serpapi.com")
    p = {"api_key": SERPAPI_KEY, **params}
    try:
        r = requests.get(BASE, params=p, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def google_search(query, num=10):
    data = _call({"engine": "google", "q": query, "num": num})
    if not data:
        return []
    return [{
        "title": r.get("title"), "url": r.get("link"),
        "snippet": r.get("snippet"), "position": r.get("position"),
    } for r in data.get("organic_results", [])[:num]]


def google_research(query, num=5):
    """Fetch organic results and related questions/searches with one API request."""
    data = _call({"engine": "google", "q": query, "num": num})
    if not data:
        return {"organic_results": [], "questions": [], "related_searches": []}
    questions = [
        item["question"] for item in data.get("related_questions", [])
        if item.get("question")
    ]
    return {
        "organic_results": [{
            "title": item.get("title"), "url": item.get("link"),
            "snippet": item.get("snippet"), "position": item.get("position"),
        } for item in data.get("organic_results", [])[:num]],
        "questions": questions,
        "related_searches": data.get("related_searches", []) or [],
    }


def people_also_ask(query):
    data = _call({"engine": "google", "q": query})
    if not data:
        return []
    questions = []
    for item in data.get("related_questions", []):
        if q := item.get("question"):
            questions.append(q)
    return questions


def related_searches(query):
    data = _call({"engine": "google", "q": query})
    if not data:
        return []
    return data.get("related_searches", []) or []


def google_trends(query, timeframe="today 12-m"):
    data = _call({"engine": "google_trends", "q": query, "date": timeframe})
    if not data:
        return {}
    return {
        "interest_over_time": data.get("interest_over_time", {}).get("timeline_data", []),
        "average": data.get("interest_over_time", {}).get("average_interest", 0),
    }


def knowledge_graph(entity):
    data = _call({"engine": "google", "q": entity})
    if not data:
        return {}
    return data.get("knowledge_graph", {}) or {}


def jtbd_research(user_problem):
    return {
        "user_problem": user_problem,
        "search_results": google_search(user_problem, num=8),
        "questions_real_users_ask": people_also_ask(user_problem),
        "alternative_phrasings": related_searches(user_problem),
        "trend_signal": google_trends(user_problem),
    }
