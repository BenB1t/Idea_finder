import os
import json
import re
import time
import requests
from typing import List, Dict, Any, Optional

DEFAULT_MODELS = {"worker": "MiniMax-M2.7", "judge": "MiniMax-M3"}

def worker_model() -> str: return os.getenv("MINIMAX_WORKER_MODEL", DEFAULT_MODELS["worker"])
def judge_model() -> str: return os.getenv("MINIMAX_JUDGE_MODEL", DEFAULT_MODELS["judge"])

def call_text(messages: List[Dict[str, str]], model: str, temperature: float = 0.1, max_tokens: int = 400) -> str:
    if os.getenv("IDEA_FINDER_MOCK", "0") == "1":
        return _mock_response(messages[-1].get("content", ""))

    key = os.getenv("MINIMAX_API_KEY") or os.getenv("LLM_API_KEY")
    if not key: raise RuntimeError("No MINIMAX_API_KEY set.")

    # MiniMax OpenAI-compatible endpoint
    base = os.getenv('MINIMAX_BASE_URL', 'https://api.minimax.chat/v1').rstrip('/')
    url = f"{base}/chat/completions"

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=120)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            raise RuntimeError(f"LLM API error {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            if attempt < 2: time.sleep(2 * (attempt + 1)); continue
            raise RuntimeError(f"LLM request failed: {e}")

def parse_json(raw: Any) -> Dict:
    if isinstance(raw, dict): return raw
    text = str(raw or "").strip().lstrip("\ufeff")
    # Reasoning models may wrap their final answer in a <think> block.
    text = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    # Decode the first complete JSON object, even if the model wrapped it in prose.
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue

    # Recover common trailing-comma mistakes while leaving other malformed output visible.
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    for index, char in enumerate(fixed):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(fixed[index:])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return {"error": "invalid_json", "raw": text[:500]}

def call_json(messages: List[Dict[str, str]], model: str, temperature: float = 0.1, max_tokens: int = 400) -> Dict:
    return parse_json(call_text(messages, model, temperature, max_tokens))

def _mock_response(prompt: str) -> str:
    if "Pain Hunter" in prompt: return json.dumps({"pain": "Mock pain found", "evidence": ["Users complain about manual work"], "confidence": 0.8})
    if "Market Validator" in prompt: return json.dumps({"paying_proof": "Competitors exist", "competitors": ["Tool A"], "pricing": "$29/mo", "confidence": 0.7})
    if "Repair" in prompt: return json.dumps({"finding": "Mock repair finding", "evidence": ["More proof"], "confidence": 0.75})
    if "Builder" in prompt: return json.dumps({"idea_name": "Mock SaaS", "concept": "Mock concept", "target_user": "Freelancers", "mvp": "Python script", "tech_stack": ["FastAPI", "Supabase"], "monetization": "$29/mo", "first_users": "Reddit"})
    if "Critic" in prompt: return json.dumps({"is_complete": False, "critique": "Mock critique", "missing_details": "Needs more specific tech stack details."})
    if "Judge" in prompt: return json.dumps({"decision": "SUBMIT", "score": 85, "reason": "Mock approval", "missing": [], "next_query": ""})
    return "{}"
