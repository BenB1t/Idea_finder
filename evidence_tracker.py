"""
Layer 2 of v2: Evidence Quality Tracker.

Every claim has metadata (source_class, age, independence, confidence).
Enforces: verified > claimed > heuristic. 5 verified claims beat 10 claimed.
"""
import os
import time
import hashlib
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from enum import Enum


class SourceClass(str, Enum):
    VERIFIED = "verified"
    CLAIMED = "claimed"
    HEURISTIC = "heuristic"


SOURCE_WEIGHTS = {
    SourceClass.VERIFIED: 1.0,
    SourceClass.CLAIMED: 0.5,
    SourceClass.HEURISTIC: 0.2,
}


def recency_factor(age_days, half_life_days=180):
    if age_days is None:
        return 1.0  # Unknown age is not decayed, but is not counted as recent.
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


@dataclass
class Claim:
    text: str
    source: str
    claim_id: str = ""
    source_url: Optional[str] = None
    source_class: str = "claimed"
    age_days: Optional[int] = 0
    independence_id: Optional[str] = None
    confidence: float = 0.5
    candidate_id: Optional[str] = None
    supporting_data: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    category: str = "general"

    def __post_init__(self):
        if not self.claim_id:
            # Claims are deduplicated within a candidate, not across candidates.
            candidate = self.candidate_id or "global"
            h = hashlib.sha256(f"{candidate}|{self.source}|{self.text}".encode()).hexdigest()[:12]
            self.claim_id = h
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @property
    def weight(self):
        cls_weight = SOURCE_WEIGHTS.get(SourceClass(self.source_class), 0.5)
        recency = recency_factor(self.age_days)
        return cls_weight * recency * max(0.0, min(1.0, self.confidence))

    def to_dict(self):
        return asdict(self)


@dataclass
class AggregateEvidence:
    candidate_id: str
    total: int = 0
    verified: int = 0
    claimed: int = 0
    heuristic: int = 0
    verified_weight: float = 0.0
    claimed_weight: float = 0.0
    heuristic_weight: float = 0.0
    composite_score: float = 0.0
    unique_sources: int = 0
    independent_signals: int = 0
    avg_age_days: float = 0.0
    pct_recent: float = 0.0
    age_unknown: int = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class GateStatus:
    candidate_id: str
    pain_proven: bool = False
    payment_proven: bool = False
    independence_ok: bool = False
    recency_ok: bool = False
    minimum_evidence: bool = False

    _ALL_GATES = ("pain_proven", "payment_proven", "independence_ok", "recency_ok", "minimum_evidence")

    @property
    def passing_gates(self):
        return [g for g in self._ALL_GATES if getattr(self, g)]

    @property
    def failing_gates(self):
        return [g for g in self._ALL_GATES if not getattr(self, g)]

    @property
    def all_pass(self):
        return len(self.failing_gates) == 0

    def to_dict(self):
        return {
            "candidate_id": self.candidate_id,
            "pain_proven": self.pain_proven, "payment_proven": self.payment_proven,
            "independence_ok": self.independence_ok, "recency_ok": self.recency_ok,
            "minimum_evidence": self.minimum_evidence,
            "passing_gates": self.passing_gates, "failing_gates": self.failing_gates,
            "all_pass": self.all_pass,
        }


class EvidenceTracker:
    def __init__(self):
        self.claims = {}
        self._by_candidate = {}

    def add(self, claim):
        if claim.claim_id in self.claims:
            return self.claims[claim.claim_id]
        self.claims[claim.claim_id] = claim
        if claim.candidate_id:
            self._by_candidate.setdefault(claim.candidate_id, []).append(claim.claim_id)
        return claim

    def add_many(self, claims):
        return [self.add(c) for c in claims]

    def for_candidate(self, candidate_id):
        return [self.claims[cid] for cid in self._by_candidate.get(candidate_id, []) if cid in self.claims]

    def aggregate(self, candidate_id):
        claims = self.for_candidate(candidate_id)
        agg = AggregateEvidence(candidate_id=candidate_id)
        if not claims:
            return agg
        agg.total = len(claims)
        classes = {"verified": 0, "claimed": 0, "heuristic": 0}
        weights = {"verified": 0.0, "claimed": 0.0, "heuristic": 0.0}
        ages = []
        recent_count = 0
        for c in claims:
            cls = c.source_class
            classes[cls] = classes.get(cls, 0) + 1
            weights[cls] = weights.get(cls, 0.0) + c.weight
            if c.age_days is not None:
                ages.append(c.age_days)
            else:
                agg.age_unknown += 1
            if c.age_days is not None and c.age_days < 90:
                recent_count += 1
        agg.verified = classes["verified"]
        agg.claimed = classes["claimed"]
        agg.heuristic = classes["heuristic"]
        agg.verified_weight = round(weights["verified"], 2)
        agg.claimed_weight = round(weights["claimed"], 2)
        agg.heuristic_weight = round(weights["heuristic"], 2)
        raw_score = (agg.verified_weight * 10 + agg.claimed_weight * 4 + agg.heuristic_weight * 1.5)
        agg.composite_score = round(min(100.0, raw_score), 1)
        indep_ids = {c.independence_id for c in claims if c.independence_id}
        sources = {c.source for c in claims}
        agg.independent_signals = len(indep_ids) if indep_ids else len(sources)
        agg.unique_sources = len(sources)
        agg.avg_age_days = round(sum(ages) / len(ages), 1) if ages else 0.0
        agg.pct_recent = round(recent_count / len(claims) * 100, 1)
        return agg

    def gate_status(self, candidate_id):
        agg = self.aggregate(candidate_id)
        claims = self.for_candidate(candidate_id)
        status = GateStatus(candidate_id=candidate_id)
        pain_claims = [c for c in claims if c.category == "pain"]
        pain_verified = sum(1 for c in pain_claims if c.source_class == "verified")
        pain_claimed = sum(1 for c in pain_claims if c.source_class == "claimed")
        status.pain_proven = pain_verified >= 3 or (pain_claimed >= 5 and pain_verified >= 1)
        payment_claims = [c for c in claims if c.category == "payment"]
        payment_verified = [c for c in payment_claims if c.source_class == "verified"]
        status.payment_proven = len(payment_verified) >= 1
        status.independence_ok = agg.independent_signals >= 3 or agg.unique_sources >= 4
        status.recency_ok = agg.pct_recent >= 50.0
        status.minimum_evidence = agg.total >= 4 and agg.composite_score >= 20.0
        return status

    def summary(self):
        candidates = list(self._by_candidate.keys())
        return {
            "total_claims": len(self.claims),
            "total_candidates": len(candidates),
            "candidates": [
                {"candidate_id": cid, "aggregate": self.aggregate(cid).to_dict(),
                 "gates": self.gate_status(cid).to_dict()}
                for cid in candidates
            ],
        }


def claim_from_github_issue(issue, candidate_id=None):
    return Claim(
        text=f"Pain signal from issue: {issue.get('title', '')}",
        source="github_issue",
        source_url=issue.get("url"),
        source_class=SourceClass.VERIFIED.value,
        age_days=_age_days_from_iso(issue.get("created_at")),
        independence_id=f"github_issue_{issue.get('url', '')}",
        confidence=0.85, candidate_id=candidate_id, category="pain",
        supporting_data={"comments": issue.get("comments", 0), "labels": issue.get("labels", [])},
    )


def claim_from_g2_review(review, candidate_id=None):
    return Claim(
        text=f"G2 review on {review.get('product', 'competitor')}: {review.get('title', '')}",
        source="g2_review",
        source_url=review.get("url"),
        source_class=SourceClass.VERIFIED.value,
        age_days=review.get("age_days"),
        confidence=0.9, candidate_id=candidate_id, category="payment",
        supporting_data={"rating": review.get("rating"), "reviewer_role": review.get("reviewer_role")},
    )


def claim_from_ph_launch(post, candidate_id=None):
    return Claim(
        text=f"Competitor launch on PH: {post.get('name')} - '{post.get('tagline')}' ({post.get('votes')} votes)",
        source="ph_launch", source_url=post.get("url"),
        source_class=SourceClass.CLAIMED.value,
        age_days=_age_days_from_iso(post.get("created_at")),
        confidence=0.7, candidate_id=candidate_id, category="competition",
        supporting_data={"votes": post.get("votes"), "comments": post.get("comments")},
    )


def claim_from_serpapi_paa(question, candidate_id=None, category="jtbd"):
    return Claim(
        text=f"User question pattern (PAA): '{question}'",
        source="google_paa", source_class=SourceClass.HEURISTIC.value,
        age_days=None,
        independence_id=f"paa_{hashlib.sha256(question.encode()).hexdigest()[:8]}",
        confidence=0.6, candidate_id=candidate_id, category=category,
    )


def claim_from_reddit_thread(thread, candidate_id=None):
    return Claim(
        text=f"Reddit thread: {thread.get('title', '')}",
        source="reddit", source_url=thread.get("url"),
        source_class=SourceClass.CLAIMED.value,
        age_days=thread.get("age_days"),
        confidence=0.5, candidate_id=candidate_id, category="pain",
        supporting_data={"score": thread.get("score"), "comments": thread.get("comments")},
    )


def _age_days_from_iso(iso):
    if not iso:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max(0, (now - dt).days)
    except Exception:
        return None


def main():
    from claim_from_serpapi_paa import claim_from_serpapi_paa  # placeholder to avoid lint
    print("See test_v2_layers.py for usage.")


if __name__ == "__main__":
    main()
