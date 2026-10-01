"""
Product Hunt GraphQL API source — launch velocity and competitor intelligence.
Free token at: https://www.producthunt.com/v2/oauth/applications
OAuth via LinkedIn works fine.
"""
import os
import requests
from typing import List, Dict, Optional

PH_TOKEN = os.getenv("PH_TOKEN", "")
ENDPOINT = "https://api.producthunt.com/v2/api/graphql"


def _query(query, variables):
    if not PH_TOKEN:
        raise RuntimeError("PH_TOKEN not set")
    try:
        r = requests.post(ENDPOINT, json={"query": query, "variables": variables},
                          headers={"Authorization": f"Bearer {PH_TOKEN}"}, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if "errors" in data:
                return None
            return data.get("data")
        return None
    except Exception:
        return None


SEARCH_POSTS = """
query SearchPosts($query: String!, $first: Int!) {
  posts(query: $query, first: $first, order: VOTES) {
    edges { node {
      id name tagline description url website votesCount commentsCount createdAt
      topics { edges { node { name } } }
    } }
  }
}
"""

GET_POST = """
query GetPost($id: ID!) {
  post(id: $id) {
    id name tagline description url website votesCount commentsCount createdAt
    topics { edges { node { name } } }
  }
}
"""


def search_launches(query, limit=15):
    data = _query(SEARCH_POSTS, {"query": query, "first": limit})
    if not data:
        return []
    return [_normalize_post(e["node"]) for e in data.get("posts", {}).get("edges", [])]


def get_post(post_id):
    data = _query(GET_POST, {"id": post_id})
    if not data or not data.get("post"):
        return None
    return _normalize_post(data["post"])


def _normalize_post(node):
    topics = node.get("topics", {}).get("edges", [])
    return {
        "id": node.get("id"), "name": node.get("name"), "tagline": node.get("tagline"),
        "description": (node.get("description") or "")[:500],
        "url": node.get("url"), "website": node.get("website"),
        "votes": node.get("votesCount", 0), "comments": node.get("commentsCount", 0),
        "created_at": node.get("createdAt"),
        "topics": [t["node"]["name"] for t in topics],
    }


def market_temperature(query):
    launches = search_launches(query, limit=20)
    if not launches:
        return {"signal": "no_data", "launch_count": 0}
    total_votes = sum(l["votes"] for l in launches)
    avg_votes = total_votes / len(launches)
    top_votes = max(l["votes"] for l in launches)
    if len(launches) >= 10 and top_votes < 100:
        signal = "saturated"
    elif len(launches) >= 10 and avg_votes > 200:
        signal = "heating"
    elif len(launches) <= 3 and avg_votes > 100:
        signal = "niche_opportunity"
    else:
        signal = "stable"
    return {"signal": signal, "launch_count": len(launches),
            "avg_votes": round(avg_votes, 1), "top_votes": top_votes,
            "top_launch": launches[0] if launches else None}