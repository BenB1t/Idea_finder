"""
Renders an OrchestratorResult as a readable Markdown dossier.
"""
from datetime import datetime
from typing import Optional

from v2_orchestrator import OrchestratorResult


GLYPHS = {
    "CONFIRMED_GO": "🟢",
    "CONDITIONAL_GO": "🟡",
    "CONFLICT": "🟠",
    "NO_GO": "🔴",
    "NEEDS_MORE_DATA": "⚪",
}


def render_dossier(result: OrchestratorResult) -> str:
    lines = []
    lines.append("# V2 Idea-Finder Dossier")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Candidate:** `{result.candidate_id}`")
    lines.append(f"**Elapsed:** {result.elapsed_seconds}s")
    lines.append(f"**Iterations:** {result.iterations}")
    lines.append("")

    spec = result.goal_spec
    lines.append("## GoalSpec")
    lines.append("")
    lines.append(f"**Target user:** {spec.target_user}")
    lines.append("")
    lines.append(f"**JTBD:** {spec.jtbd}")
    lines.append("")
    lines.append(f"**Success signal:** {spec.success_signal}")
    if spec.constraints:
        lines.append(f"")
        lines.append(f"**Constraints:** {', '.join(spec.constraints)}")
    if spec.anti_goals:
        lines.append(f"**Anti-goals:** {', '.join(spec.anti_goals)}")
    if spec.founder_fit:
        lines.append(f"**Founder fit:** {', '.join(spec.founder_fit)}")
    lines.append("")

    if result.combined_verdict:
        v = result.combined_verdict
        glyph = GLYPHS.get(v.final_verdict, "•")
        lines.append("---")
        lines.append("")
        lines.append(f"## Verdict: {glyph} {v.final_verdict}")
        lines.append("")
        lines.append(f"**Confidence:** {v.confidence:.2f}")
        lines.append(f"")
        lines.append(f"**Summary:** {v.summary}")
        lines.append("")

        if v.gate_decision:
            gd = v.gate_decision
            lines.append(f"### Gate decision: `{gd.verdict}` (conf {gd.confidence:.2f})")
            lines.append("")
            if gd.blocking_reasons:
                lines.append("**Blocking reasons:**")
                lines.append("")
                for r in gd.blocking_reasons:
                    lines.append(f"- {r}")
                lines.append("")
            if gd.caveats:
                lines.append("**Caveats:**")
                lines.append("")
                for c in gd.caveats:
                    lines.append(f"- {c}")
                lines.append("")

        if v.verifier_verdict:
            vv = v.verifier_verdict
            vstatus = "✅ PASS" if vv.passed else "❌ REJECT"
            lines.append(f"### Verifier ({vv.model_used}): {vstatus}")
            lines.append("")
            if vv.would_tell_founder:
                lines.append(f"> {vv.would_tell_founder}")
                lines.append("")
            if vv.failing_gates:
                lines.append(f"**Failing gates:** {', '.join(vv.failing_gates)}")
            if vv.blocking_question:
                lines.append(f"**Blocking question:** {vv.blocking_question}")
            lines.append("")

    if result.tracker_summary:
        agg = result.tracker_summary.get("aggregate", {})
        gates = result.tracker_summary.get("gates", {})

        lines.append("---")
        lines.append("")
        lines.append("## Evidence")
        lines.append("")
        lines.append(f"- **Total claims:** {agg.get('total', 0)}")
        lines.append(f"- **Verified:** {agg.get('verified', 0)}")
        lines.append(f"- **Claimed:** {agg.get('claimed', 0)}")
        lines.append(f"- **Heuristic:** {agg.get('heuristic', 0)}")
        lines.append(f"- **Composite score:** {agg.get('composite_score', 0)}/100")
        lines.append(f"- **Independent signals:** {agg.get('independent_signals', 0)}")
        lines.append(f"- **Unique sources:** {agg.get('unique_sources', 0)}")
        lines.append(f"- **Avg age:** {agg.get('avg_age_days', 0)} days")
        lines.append(f"- **Claims with unknown age:** {agg.get('age_unknown', 0)}")
        lines.append(f"- **Recent (<90d):** {agg.get('pct_recent', 0)}%")
        lines.append("")

        lines.append("### Gate audit")
        lines.append("")
        lines.append("| Gate | Status |")
        lines.append("|------|--------|")
        for gate in ["pain_proven", "payment_proven", "independence_ok", "recency_ok", "minimum_evidence"]:
            passing = gate in gates.get("passing_gates", [])
            status = "✅ pass" if passing else "❌ fail"
            lines.append(f"| `{gate}` | {status} |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    if result.sources_run:
        lines.append(f"**Run:** {', '.join(result.sources_run)}")
    else:
        lines.append("**Run:** _(none)_")
    if result.sources_skipped:
        lines.append(f"")
        lines.append(f"**Skipped:** {', '.join(result.sources_skipped)}")
    lines.append("")

    if result.combined_verdict and result.combined_verdict.key_concerns:
        lines.append("---")
        lines.append("")
        lines.append("## Concerns")
        lines.append("")
        for c in result.combined_verdict.key_concerns:
            lines.append(f"- {c}")
        lines.append("")

    next_steps = []
    if result.combined_verdict and result.combined_verdict.gate_decision:
        next_steps.extend(result.combined_verdict.gate_decision.next_steps)

    if next_steps:
        lines.append("---")
        lines.append("")
        lines.append("## Next steps")
        lines.append("")
        for i, s in enumerate(next_steps, 1):
            lines.append(f"{i}. {s}")
        lines.append("")

    return "\n".join(lines)


def main():
    from goal_parser import GoalSpec
    from v2_orchestrator import V2Orchestrator
    from evidence_tracker import Claim, claim_from_serpapi_paa

    spec = GoalSpec(
        raw_goal="invoice chasing tool",
        target_user="Head of Operations at a 20-person digital agency",
        jtbd="When a client pays an invoice late, I want to auto-send a reminder, so I can stop chasing payments manually.",
        success_signal="20 paying users at $49/mo within 30 days",
        constraints=["solo buildable in 3 weeks"],
        is_complete=True,
    )

    orch = V2Orchestrator(spec, candidate_id="dossier_demo")
    for i, t in enumerate(["Manual invoice reminders", "Wish Stripe had trigger", "Need auto-chase"]):
        orch.tracker.add(Claim(text=f"Pain: {t}", source="github_issue", source_class="verified",
                                age_days=30, category="pain", confidence=0.85,
                                independence_id=f"gh_{i}", candidate_id=orch.candidate_id))
    orch.tracker.add(Claim(text="G2: FreshBooks customers pay $50/mo", source="g2_review",
                            source_class="verified", age_days=14, category="payment", confidence=0.9,
                            independence_id="g2_1", candidate_id=orch.candidate_id))
    for name in ["InvoiceButler", "PayPing"]:
        orch.tracker.add(Claim(text=f"PH: {name}", source="ph_launch", source_class="claimed",
                                age_days=60, category="competition", confidence=0.7,
                                independence_id=f"ph_{name}", candidate_id=orch.candidate_id))
    for q in ["how to auto-send invoice reminders", "best software chasing late payments"]:
        orch.tracker.add(claim_from_serpapi_paa(q, candidate_id=orch.candidate_id))

    result = orch.run()
    print(render_dossier(result))


if __name__ == "__main__":
    main()
