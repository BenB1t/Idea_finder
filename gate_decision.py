"""
Layer 3 of v2: Hard Gate Check + Decision Verdict.

Wraps EvidenceTracker into a clean 4-way verdict:
  GO             — all gates pass + strong verified evidence
  CONDITIONAL_GO — all gates pass but evidence is thin
  NO_GO          — at least one hard gate failed
  NEEDS_MORE_DATA — not enough evidence to even evaluate
"""
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

from evidence_tracker import EvidenceTracker, GateStatus, AggregateEvidence


VERDICTS = ("GO", "CONDITIONAL_GO", "NO_GO", "NEEDS_MORE_DATA")


@dataclass
class Decision:
    candidate_id: str
    verdict: str
    confidence: float = 0.0
    blocking_reasons: List[str] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    summary: str = ""
    gate_status: Optional[Dict] = None
    aggregate: Optional[Dict] = None

    def to_dict(self) -> Dict:
        return asdict(self)


MIN_COMPOSITE_FOR_EVAL = 20.0
MIN_CLAIMS_FOR_EVAL = 3
SOFT_VERIFIED_FLOOR = 4
SOFT_COMPOSITE_FLOOR = 50.0


NEXT_STEPS_FOR_GATE = {
    "pain_proven": [
        "Find >=3 verified pain sources (GitHub issues, G2 negative reviews of adjacent tools, support threads).",
        "Or scrape Reddit/HN with skepticism filter for complaint patterns.",
    ],
    "payment_proven": [
        "Find a verified source showing someone pays for the current workaround.",
        "Best sources: G2 reviews with pricing mentions, Stripe public pricing pages, PH launches with revenue claims.",
    ],
    "independence_ok": [
        "Add evidence from at least 3 different source types (e.g., GitHub + G2 + Reddit).",
        "Check that claims aren't just echoing the same Reddit thread 50 times.",
    ],
    "recency_ok": [
        "Most evidence is >90 days old. Look for fresher signals (recent GitHub issues, this month's G2 reviews).",
    ],
    "minimum_evidence": [
        f"Need at least {MIN_CLAIMS_FOR_EVAL} distinct claims with composite score >=20. Currently too thin.",
        "Add more sources before evaluating.",
    ],
}


def evaluate(tracker, candidate_id):
    claims = tracker.for_candidate(candidate_id)
    agg = tracker.aggregate(candidate_id)
    gates = tracker.gate_status(candidate_id)

    decision = Decision(
        candidate_id=candidate_id, verdict="",
        gate_status=gates.to_dict(), aggregate=agg.to_dict(),
    )

    if agg.total < MIN_CLAIMS_FOR_EVAL or agg.composite_score < MIN_COMPOSITE_FOR_EVAL:
        decision.verdict = "NEEDS_MORE_DATA"
        decision.confidence = 0.0
        decision.summary = (f"Insufficient evidence: {agg.total} claims, composite {agg.composite_score}/100. "
                            f"Need >={MIN_CLAIMS_FOR_EVAL} claims with composite >={MIN_COMPOSITE_FOR_EVAL}.")
        decision.next_steps = [
            "Gather more evidence before evaluating.",
            "Start with: GitHub issues matching the problem, SerpAPI 'people also ask' for the JTBD, PH launches in adjacent space.",
        ]
        if gates.failing_gates:
            decision.next_steps.extend(NEXT_STEPS_FOR_GATE.get(g, []) for g in gates.failing_gates)
            decision.next_steps = [s for sub in decision.next_steps for s in (sub if isinstance(sub, list) else [sub])]
        return decision

    if not gates.all_pass:
        decision.verdict = "NO_GO"
        decision.confidence = 0.0
        for g in gates.failing_gates:
            decision.blocking_reasons.append(_explain_gate_failure(g, agg, claims))
            decision.next_steps.extend(NEXT_STEPS_FOR_GATE.get(g, []))
        decision.summary = (f"NO-GO: {len(gates.failing_gates)} gate(s) failed — "
                            f"{', '.join(gates.failing_gates)}")
        return decision

    verified_count = agg.verified
    needs_more_verified = verified_count < SOFT_VERIFIED_FLOOR
    thin_composite = agg.composite_score < SOFT_COMPOSITE_FLOOR

    if needs_more_verified or thin_composite:
        decision.verdict = "CONDITIONAL_GO"
        decision.confidence = round(min(0.7, agg.composite_score / 100), 2)
        if needs_more_verified:
            decision.caveats.append(f"Only {verified_count} verified claims — recommend >={SOFT_VERIFIED_FLOOR} for high confidence.")
        if thin_composite:
            decision.caveats.append(f"Composite score {agg.composite_score}/100 — recommend >={SOFT_COMPOSITE_FLOOR} before committing.")
        decision.summary = (f"Conditional GO: gates pass, but evidence is thin "
                            f"({verified_count} verified, {agg.composite_score}/100 composite). "
                            f"Proceed with these caveats or gather more evidence first.")
        return decision

    decision.verdict = "GO"
    decision.confidence = round(min(0.95, 0.5 + agg.verified_weight * 0.1), 2)
    decision.summary = (f"GO: all gates pass with strong evidence "
                        f"({verified_count} verified claims, composite {agg.composite_score}/100). "
                        f"Eligible for Adversarial Verifier.")
    return decision


def _explain_gate_failure(gate, agg, claims):
    if gate == "pain_proven":
        pain_v = sum(1 for c in claims if c.category == "pain" and c.source_class == "verified")
        pain_c = sum(1 for c in claims if c.category == "pain" and c.source_class == "claimed")
        return f"pain_proven failed: {pain_v} verified + {pain_c} claimed pain claims (need >=3 verified, or >=5 claimed + >=1 verified)"
    if gate == "payment_proven":
        return "payment_proven failed: no verified claim shows someone paying for a current workaround"
    if gate == "independence_ok":
        return f"independence_ok failed: only {agg.independent_signals} independent signals, {agg.unique_sources} unique sources (need >=3 signals or >=4 sources)"
    if gate == "recency_ok":
        return f"recency_ok failed: only {agg.pct_recent}% of claims are <90 days old (need >=50%)"
    if gate == "minimum_evidence":
        return f"minimum_evidence failed: {agg.total} claims with composite {agg.composite_score}/100 (need >=5 claims with score >=20)"
    return f"{gate} failed"


VERDICT_GLYPHS = {
    "GO": "🟢",
    "CONDITIONAL_GO": "🟡",
    "NO_GO": "🔴",
    "NEEDS_MORE_DATA": "⚪",
}


def format_decision(d):
    lines = [f"{VERDICT_GLYPHS.get(d.verdict, '-')} {d.verdict} (confidence: {d.confidence:.2f})",
             f"   {d.summary}"]
    if d.blocking_reasons:
        lines.append("")
        lines.append("   Blocking reasons:")
        for r in d.blocking_reasons:
            lines.append(f"     - {r}")
    if d.caveats:
        lines.append("")
        lines.append("   Caveats:")
        for c in d.caveats:
            lines.append(f"     - {c}")
    if d.next_steps:
        lines.append("")
        lines.append("   Next steps:")
        for s in d.next_steps:
            lines.append(f"     -> {s}")
    return "\n".join(lines)


def evaluate_and_print(tracker, candidate_id):
    d = evaluate(tracker, candidate_id)
    print(format_decision(d))
    return d


def main():
    from evidence_tracker import Claim
    print("=" * 70)
    print("Gate Decision Layer — demo")
    print("=" * 70)
    tracker = EvidenceTracker()
    cid_a = "invoice_chaser_strong"
    for i in range(6):
        tracker.add(_fake_claim(f"github pain {i}", "github_issue", "verified", 30, "pain", candidate_id=cid_a))
    tracker.add(_fake_claim("G2 review pricing", "g2_review", "verified", 14, "payment", candidate_id=cid_a))
    tracker.add(_fake_claim("PH launch A", "ph_launch", "claimed", 60, "competition", candidate_id=cid_a))
    tracker.add(_fake_claim("PAA how to", "google_paa", "heuristic", 0, "jtbd", candidate_id=cid_a))
    tracker.add(_fake_claim("PAA best tool", "google_paa", "heuristic", 0, "jtbd", candidate_id=cid_a))
    cid_b = "vague_idea_weak"
    for i in range(5):
        tracker.add(_fake_claim(f"reddit pain {i}", "reddit", "claimed", 30, "pain", candidate_id=cid_b))
    cid_c = "untested_idea"
    tracker.add(_fake_claim("one weak claim", "reddit", "claimed", 30, "pain", candidate_id=cid_c))
    print(f"\n--- Candidate A: {cid_a} ---")
    evaluate_and_print(tracker, cid_a)
    print(f"\n--- Candidate B: {cid_b} ---")
    evaluate_and_print(tracker, cid_b)
    print(f"\n--- Candidate C: {cid_c} ---")
    evaluate_and_print(tracker, cid_c)


def _fake_claim(text, source, cls, age, category, candidate_id=None):
    from evidence_tracker import Claim
    return Claim(text=text, source=source, source_class=cls, age_days=age,
                category=category, confidence=0.8, independence_id=f"{source}_{text}",
                candidate_id=candidate_id)


if __name__ == "__main__":
    main()
