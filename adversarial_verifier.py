"""
Layer 4 of v2: Adversarial Verifier.

A separate LLM call (M2.7 by default) that re-evaluates the candidate
against the 6 hard gates with rejection-pressure.
"""
import json
import re
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

from goal_parser import GoalSpec
from evidence_tracker import EvidenceTracker, Claim, AggregateEvidence
from gate_decision import Decision, evaluate, VERDICTS

try:
    from minimax_client import call_json
except ImportError:
    call_json = None


VERIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "REJECT"]},
        "failing_gates": {"type": "array", "items": {"type": "string"}},
        "key_concerns": {"type": "array", "items": {"type": "string"}},
        "would_tell_founder": {"type": "string"},
        "blocking_question": {"type": "string"},
    },
    "required": ["verdict", "would_tell_founder"],
}


@dataclass
class VerifierVerdict:
    candidate_id: str
    passed: bool
    confidence: float = 0.0
    failing_gates: List[str] = field(default_factory=list)
    key_concerns: List[str] = field(default_factory=list)
    would_tell_founder: str = ""
    blocking_question: str = ""
    raw_response: str = ""
    model_used: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


COMBINED_VERDICTS = ("CONFIRMED_GO", "CONDITIONAL_GO", "CONFLICT", "NO_GO", "NEEDS_MORE_DATA")


@dataclass
class CombinedVerdict:
    candidate_id: str
    final_verdict: str
    confidence: float = 0.0
    gate_decision: Optional[Decision] = None
    verifier_verdict: Optional[VerifierVerdict] = None
    summary: str = ""
    key_concerns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            **asdict(self),
            "gate_decision": self.gate_decision.to_dict() if self.gate_decision else None,
            "verifier_verdict": self.verifier_verdict.to_dict() if self.verifier_verdict else None,
        }


VERIFIER_SYSTEM_PROMPT = """You are a skeptical VC partner reviewing a micro-SaaS pitch.

You have heard 10,000 pitches. You are tired. You want to see ONE NUMBER that doesn't lie.

Default to REjection. The cost of a false PASS (you let a bad pitch through) is
much higher than a false REJECT (you turn down a mediocre idea). When in doubt,
REJECT and ask the blocking_question.

Every claim in the evidence must be evaluated by its source class:
  - verified   = direct from a real citable source (GitHub issue, G2 verified review, Stripe page)
  - claimed    = self-reported (Reddit, Indie Hackers, Product Hunt MRR claims)
  - heuristic  = inferred (Google PAA, search trends, related searches)

Claims of class "claimed" alone are NOT proof of anything. They are anecdotes.

THE 6 GATES (any failure = REJECT):

GATE 1: PAIN_PROVEN
   >=3 INDEPENDENT VERIFIED sources showing real users have this specific pain.
   Bad: "Lots of Reddit threads mention this" (Reddit = claimed class, not verified)
   Bad: 3 sources all echoing the same complaint (1 signal, not 3)
   Good: GitHub issue + G2 negative review + support thread, all on different problems

GATE 2: PAYMENT_PROVEN
   >=1 VERIFIED source showing someone currently pays for a workaround.
   Bad: "PH launch with 500 upvotes" (upvotes != paying customers, claimed class)
   Good: G2 review with "I pay $49/mo for X" quote from a verified reviewer

GATE 3: USER_REACHABLE
   Founder can name 3 specific places target users congregate + has access to them.
   Bad: "We can run Facebook ads" (everyone can)
   Good: "I have 47 warm intros from my previous job at [company]"

GATE 4: JTBD_CRISP
   Passes the feature-vs-job test. NOT "tool for X" (that's a feature).
   Good: "When [trigger], I want to [action], so I can [outcome]"

GATE 5: MVP_PLAUSIBLE
   Solo buildable in <=3 weeks given the founder's stated skills.
   Bad: "We need to train an LLM and integrate 4 APIs and build a Slack app" (3 months)
   Good: "1 weekend using existing well-documented APIs"

GATE 6: DIFFERENTIATION
   At least one angle no current competitor has claimed.
   Bad: "AI version of existing tool" (commodity claim, no moat)
   Good: "Only tool that works offline" / "Only one with [specific integration]"

REJECTION EXAMPLES:

[bad] "Tool that helps freelancers manage their taxes"
   -> REJECT on GATE 1. "Freelancers" = 50M people, no specific user, no proof of pain.
   -> REJECT on GATE 3. Founder has no specific channel to reach them.

[bad] "AI writing assistant for content creators"
   -> REJECT on GATE 6. Grammarly, Jasper, Copy.ai, 50 others. Zero differentiation.
   -> REJECT on GATE 2. No verified payment signal that anyone will switch.

[bad] "Dashboard for small business owners"
   -> REJECT on GATE 4. "Dashboard" is a feature, not a job.
   -> REJECT on GATE 1. "Small business" is not a specific user.

CONDITIONAL ACCEPT EXAMPLES:

[ok] "Slack-bot that posts daily standup summary to Notion for engineering managers
   at 10-50 person startups who currently run standups in Slack threads that get lost.
   Evidence: 47 r/SysAdmin threads about this exact pain (claimed), 3 competitors
   charging $49-$99/mo with 800+ combined paying customers on PH (claimed, needs verify)."
   -> CONDITIONAL PASS on GATES 1, 2, 4, 6. But GATE 2 is 'claimed' not verified --
   need to confirm those PH customers are actually paying. blocking_question.

[ok] "Stripe webhook -> auto-send invoice reminder when 3+ days late, for freelancers
   using Stripe. Evidence: 12 GitHub issues on stripe-node asking for this exact
   trigger (verified), 4 G2 reviews of FreshBooks mentioning 'chasing payments' as
   #1 pain (verified), 2 PH launches in adjacent space with revenue screenshots
   (claimed)."
   -> PASS on all 6 gates. Pain verified. Payment verified. JTBD crisp. MVP plausible.
   Differentiation: only tool that uses Stripe's own webhook for this trigger.

OUTPUT FORMAT (strict JSON, no other text):
{
  "verdict": "PASS" or "REJECT",
  "failing_gates": ["gate_name", ...],     // empty list if PASS
  "key_concerns": ["specific concern 1", ...],
  "would_tell_founder": "ONE sentence you'd actually say to them",
  "blocking_question": "the single question that, if answered, would change your mind (empty string if PASS)"
}

Be specific. Vague concerns = useless concerns."""


def build_verifier_prompt(candidate_id, candidate_summary, goal_spec, agg, top_claims):
    lines = []
    lines.append(f"CANDIDATE ID: {candidate_id}")
    lines.append("")
    lines.append("CANDIDATE SUMMARY:")
    for k, v in candidate_summary.items():
        if isinstance(v, list):
            lines.append(f"  {k}: {', '.join(str(x) for x in v)}")
        else:
            lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("USER'S GOALSPEC:")
    lines.append(f"  Target user: {goal_spec.target_user}")
    lines.append(f"  JTBD: {goal_spec.jtbd}")
    lines.append(f"  Success signal: {goal_spec.success_signal}")
    if goal_spec.constraints:
        lines.append(f"  Constraints: {', '.join(goal_spec.constraints)}")
    if goal_spec.anti_goals:
        lines.append(f"  Anti-goals: {', '.join(goal_spec.anti_goals)}")
    if goal_spec.founder_fit:
        lines.append(f"  Founder fit: {', '.join(goal_spec.founder_fit)}")
    lines.append("")
    lines.append("EVIDENCE SUMMARY:")
    lines.append(f"  Total claims: {agg.total}")
    lines.append(f"  Verified: {agg.verified} | Claimed: {agg.claimed} | Heuristic: {agg.heuristic}")
    lines.append(f"  Composite score: {agg.composite_score}/100")
    lines.append(f"  Independent signals: {agg.independent_signals}")
    lines.append(f"  Unique sources: {agg.unique_sources}")
    lines.append("")
    lines.append(f"TOP {min(5, len(top_claims))} CLAIMS BY WEIGHT:")
    for c in top_claims[:5]:
        lines.append(f"  [{c.source_class:8}] {c.text[:120]} (weight={c.weight:.2f}, age={c.age_days}d)")
    lines.append("")
    lines.append("Apply the 6 gates. Default to REJECT. Return JSON only.")
    return "\n".join(lines)


def _get_verifier_model() -> str:
    import os
    return os.getenv("MINIMAX_VERIFIER_MODEL", "MiniMax-M2.7")


def verify(candidate_id, candidate_summary, goal_spec, tracker, skip_if_needs_data=True):
    claims = tracker.for_candidate(candidate_id)
    agg = tracker.aggregate(candidate_id)
    top_claims = sorted(claims, key=lambda c: c.weight, reverse=True)[:5]
    model = _get_verifier_model()
    prompt = build_verifier_prompt(candidate_id, candidate_summary, goal_spec, agg, top_claims)

    if call_json is None:
        return VerifierVerdict(
            candidate_id=candidate_id, passed=False, confidence=0.0,
            failing_gates=["verifier_unavailable"],
            key_concerns=["Adversarial verifier could not run — LLM client not configured."],
            would_tell_founder="Could not verify — please check LLM configuration.",
            blocking_question="", model_used="none",
        )

    try:
        response = call_json(
            [{"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
             {"role": "user", "content": prompt}],
            model, temperature=0.2, max_tokens=600,
        )
    except Exception as e:
        return VerifierVerdict(
            candidate_id=candidate_id, passed=False, confidence=0.0,
            failing_gates=["verifier_error"],
            key_concerns=[f"Verifier call failed: {e}"],
            would_tell_founder="Verifier errored out — proceed with caution.",
            blocking_question="", model_used=model,
        )

    raw_text = response.get("raw", "") if isinstance(response, dict) else ""
    verdict_str = str(response.get("verdict", "REJECT")).upper().strip()
    passed = verdict_str == "PASS"
    failing = response.get("failing_gates", []) or []
    if not isinstance(failing, list):
        failing = []
    concerns = response.get("key_concerns", []) or []
    if not isinstance(concerns, list):
        concerns = [str(concerns)]

    return VerifierVerdict(
        candidate_id=candidate_id, passed=passed,
        confidence=round(min(0.95, 0.5 + (0.1 if passed else -0.1) + agg.verified_weight * 0.05), 2),
        failing_gates=failing, key_concerns=concerns,
        would_tell_founder=response.get("would_tell_founder", ""),
        blocking_question=response.get("blocking_question", ""),
        raw_response=raw_text, model_used=model,
    )


def combine(gate_decision, verifier=None):
    gv = gate_decision.verdict
    if verifier is None:
        return CombinedVerdict(
            candidate_id=gate_decision.candidate_id,
            final_verdict=gv if gv in COMBINED_VERDICTS else "NO_GO",
            confidence=gate_decision.confidence,
            gate_decision=gate_decision, verifier_verdict=None,
            summary=f"{gv} (no verifier run)",
        )

    if gv == "GO" and verifier.passed:
        final, conf, summary = "CONFIRMED_GO", round(min(0.98, (gate_decision.confidence + verifier.confidence) / 2 + 0.05), 2), "Both gate-decision and adversarial verifier agree: strong GO."
    elif gv == "GO" and not verifier.passed:
        final, conf, summary = "CONFLICT", 0.5, "Gate-decision says GO but verifier says REJECT. Needs human review."
    elif gv == "CONDITIONAL_GO" and verifier.passed:
        final, conf, summary = "CONDITIONAL_GO", round((gate_decision.confidence + verifier.confidence) / 2, 2), "Conditional GO confirmed by verifier. Proceed with caveats."
    elif gv == "CONDITIONAL_GO" and not verifier.passed:
        final, conf, summary = "NO_GO", round((gate_decision.confidence + (1 - verifier.confidence)) / 2, 2), "Verifier rejected a candidate gate-decision thought was conditional. Downgraded to NO_GO."
    elif gv == "NO_GO":
        final, conf, summary = "NO_GO", gate_decision.confidence, f"Gate-decision NO_GO ({len(gate_decision.blocking_reasons)} reasons)."
    elif gv == "NEEDS_MORE_DATA":
        final, conf, summary = "NEEDS_MORE_DATA", 0.0, "Not enough evidence to evaluate. Gather more."
    else:
        final, conf, summary = "NO_GO", 0.3, f"Unexpected gate verdict: {gv}"

    return CombinedVerdict(
        candidate_id=gate_decision.candidate_id, final_verdict=final,
        confidence=conf, gate_decision=gate_decision, verifier_verdict=verifier,
        summary=summary, key_concerns=verifier.key_concerns,
    )


def evaluate_with_verifier(candidate_id, candidate_summary, goal_spec, tracker):
    gate_decision = evaluate(tracker, candidate_id)
    if gate_decision.verdict in ("NEEDS_MORE_DATA", "NO_GO"):
        return combine(gate_decision, verifier=None)
    verifier = verify(candidate_id, candidate_summary, goal_spec, tracker)
    return combine(gate_decision, verifier)


GLYPHS = {
    "CONFIRMED_GO": "🟢",
    "CONDITIONAL_GO": "🟡",
    "CONFLICT": "🟠",
    "NO_GO": "🔴",
    "NEEDS_MORE_DATA": "⚪",
}


def format_combined(cv):
    glyph = GLYPHS.get(cv.final_verdict, "•")
    lines = [f"{glyph} {cv.final_verdict} (confidence: {cv.confidence:.2f})",
             f"   {cv.summary}"]
    if cv.gate_decision:
        lines.append(f"   Gate decision: {cv.gate_decision.verdict} (conf {cv.gate_decision.confidence:.2f})")
    if cv.verifier_verdict:
        v = cv.verifier_verdict
        vglyph = "[ok]" if v.passed else "[bad]"
        lines.append(f"   Verifier ({v.model_used}): {vglyph} {'PASS' if v.passed else 'REJECT'} (conf {v.confidence:.2f})")
        if v.would_tell_founder:
            lines.append(f'   "{v.would_tell_founder}"')
        if v.failing_gates:
            lines.append(f"   Failing gates: {', '.join(v.failing_gates)}")
        if v.blocking_question:
            lines.append(f"   Blocking question: {v.blocking_question}")
    if cv.key_concerns:
        lines.append(f"   Concerns:")
        for c in cv.key_concerns:
            lines.append(f"     - {c}")
    return "\n".join(lines)


def main():
    from goal_parser import GoalSpec
    from evidence_tracker import EvidenceTracker, Claim, claim_from_serpapi_paa

    spec = GoalSpec(
        raw_goal="invoice chaser",
        target_user="Head of Ops at a 20-person digital agency",
        jtbd="When a client pays an invoice late, I want to auto-send a reminder, so I can stop chasing payments manually.",
        success_signal="20 paying users at $49/mo within 30 days",
        constraints=["solo buildable in 3 weeks", "uses Stripe"],
        anti_goals=["no marketplace", "no AI wrapper"],
        founder_fit=["10 years in agency finance"],
        is_complete=True,
    )

    tracker = EvidenceTracker()
    cid_a = "invoice_chaser_strong"
    for i in range(6):
        tracker.add(Claim(text=f"github pain {i}", source="github_issue", source_class="verified",
                          age_days=30, category="pain", confidence=0.85,
                          independence_id=f"gh_{i}", candidate_id=cid_a))
    tracker.add(Claim(text="G2 pricing", source="g2_review", source_class="verified",
                      age_days=14, category="payment", confidence=0.9,
                      independence_id="g2", candidate_id=cid_a))
    tracker.add(Claim(text="PH launch", source="ph_launch", source_class="claimed",
                      age_days=60, category="competition", confidence=0.7,
                      independence_id="ph", candidate_id=cid_a))
    tracker.add(Claim(text="PAA", source="google_paa", source_class="heuristic",
                      age_days=0, category="jtbd", confidence=0.6,
                      independence_id="paa", candidate_id=cid_a))

    candidate_summary_a = {
        "name": "PayPing",
        "tagline": "Stripe-native late-invoice reminder for freelancers",
        "mvp": "Single Lambda + Stripe webhook + email template",
        "pricing": "$29/mo per connected Stripe account",
        "competitors": ["FreshBooks", "QuickBooks", "InvoiceButler"],
    }

    print("\n--- Candidate A ---")
    cv_a = evaluate_with_verifier(cid_a, candidate_summary_a, spec, tracker)
    print(format_combined(cv_a))


if __name__ == "__main__":
    main()