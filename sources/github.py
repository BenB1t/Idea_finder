"""
GitHub REST API source — star velocity, adjacent OSS tools, recent issues.
Free, just need a Personal Access Token.
Setup: https://github.com/settings/tokens -> fine-grained, public_repo read.
"""
import os
import time
import requests
from typing import List, Dict, Optional

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
BASE = "https://api.github.com"


def _headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def _get(path, params=None):
    try:
        r = requests.get(f"{BASE}{path}", headers=_headers(), params=params or {}, timeout=30)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def get_repo(owner, repo):
    return _get(f"/repos/{owner}/{repo}")


def search_repositories(query, sort="stars", order="desc", per_page=10):
    data = _get("/search/repositories", {"q": query, "sort": sort, "order": order, "per_page": per_page})
    if not data:
        return []
    return [{
        "full_name": item.get("full_name"), "description": item.get("description"),
        "stars": item.get("stargazers_count"), "forks": item.get("forks_count"),
        "language": item.get("language"), "last_push": item.get("pushed_at"),
        "url": item.get("html_url"),
    } for item in data.get("items", [])]


def get_recent_issues(owner, repo, days=90, labels=None):
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))
    params = {"since": since, "state": "all", "per_page": 50}
    if labels:
        params["labels"] = labels
    data = _get(f"/repos/{owner}/{repo}/issues", params)
    if not data:
        return []
    issues = [i for i in data if "pull_request" not in i]
    return [{
        "title": i.get("title"), "body": (i.get("body") or "")[:500],
        "state": i.get("state"), "labels": [l.get("name") for l in i.get("labels", [])],
        "created_at": i.get("created_at"), "comments": i.get("comments"),
        "url": i.get("html_url"),
    } for i in issues]


def star_velocity_proxy(owner, repo):
    data = get_repo(owner, repo)
    if not data:
        return {}
    return {
        "full_name": data.get("full_name"), "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"), "open_issues": data.get("open_issues_count"),
        "last_pushed": data.get("pushed_at"), "language": data.get("language"),
        "description": data.get("description"), "url": data.get("html_url"),
    }


def adjacent_tools(query, top_n=10):
    return search_repositories(query, sort="stars", per_page=top_n)