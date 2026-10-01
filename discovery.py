"""Discovery-first workflow for broad SaaS opportunity searches."""
from dataclasses import dataclass, field
from typing import Dict, List
import uuid
import re

from goal_parser import GoalSpec
from minimax_client import call_json, worker_model
from gate_decision import evaluate
from evidence_tracker import Claim
from v2_orchestrator import V2Orchestrator, _derive_queries
from deep_research import research_hypothesis as conduct_research


HYPOTHESIS_PROMPT = """You are a product discovery researcher. The user has a broad area of interest,
not a defined product idea. Generate distinct, testable SaaS opportunity hypotheses.

USER'S AREA OF INTEREST:
<goal>
{goal}
</goal>

Keep the stated market as the center of the search. Generate exactly {focused_count} candidates
for that market and at most {adjacent_count} adjacent-market candidates. Do not let founder skills
replace the user's chosen market. Poor or irrelevant search results are not evidence that the
market is dead; keep researching within it. Explore adjacent markets only after the main market has
relevant evidence against it or appears inaccessible after multiple targeted searches.

OPTIONAL FOUNDER CONTEXT:
{founder_context}

PREVIOUS CANDIDATES AND WHAT THEIR RESEARCH FOUND (avoid repeats; use failures to redirect):
{previous_candidates}

Return only a JSON object with this shape:
{{
  "candidates": [
    {{
      "name": "short opportunity name",
      "target_user": "specific role, situation, and context",
      "problem": "a recurring costly or frustrating job",
      "jtbd": "When [trigger], I want to [action], so I can [outcome].",
      "buyer": "who would pay and why",
      "why_software": "why software could improve the current workaround",
      "assumptions": ["unknowns that require research"],
      "first_validation": "a concrete low-cost test with target users"
    }}
  ]
}}

Generate exactly {count} candidates. Make the target users, problems, and proposed value
meaningfully different. Among the {focused_count} in-market candidates, cover different buyer roles
and jobs. Prefer reachable niches with frequent, costly problems. Fit acquisition
to the founder context: do not assume an existing audience, and prefer customer groups discoverable
through public directories, communities, or low-cost direct outreach when the founder has no audience.
Do not recommend paid ads unless the stated budget supports an actual test. Do not invent
market statistics, customer quotes, sources, founder skills, success metrics, or proof of willingness
to pay. Do not add ages, prices, revenue, user counts, time savings, or frequency estimates unless
the user supplied them. Do not invent sample sizes in the suggested first test. Treat broad claims such as "I can reach any audience" as unproven; name concrete channels
only when the user provides them. Do not propose opportunities whose core depends on illegal
conduct, deception, bypassing access controls, or causing harm. Keep each field concise and return
valid JSON only."""


@dataclass
class OpportunityHypothesis:
    name: str
    target_user: str
    problem: str
    jtbd: str
    buyer: str
    why_software: str
    assumptions: List[str] = field(default_factory=list)
    first_validation: str = ""
    unverified_estimates: List[str] = field(default_factory=list)

    def to_spec(self, raw_goal: str) -> GoalSpec:
        return GoalSpec(
            raw_goal=raw_goal,
            target_user=self.target_user,
            jtbd=self.jtbd,
            success_signal="Not established; requires customer validation.",
            is_complete=True,
        )


@dataclass
class ResearchedOpportunity:
    hypothesis: OpportunityHypothesis
    candidate_id: str
    claims: List[Dict]
    aggregate: Dict
    gates: Dict
    decision: Dict
    sources_run: List[str]
    sources_skipped: List[str]
    research_analysis: Dict = field(default_factory=dict)
    source_findings: List[Dict] = field(default_factory=list)


@dataclass
class DiscoveryReport:
    raw_goal: str
    opportunities: List[ResearchedOpportunity]
    research_satisfied: bool = False
    stop_reason: str = ""


def _clean_string(value, default=""):
    return value.strip() if isinstance(value, str) else default


def _unverified_numbers(values, user_context):
    """Flag generated numeric claims unless the same number came from the user's prompt."""
    supplied_numbers = set(re.findall(r"\d[\d,]*", user_context))
    patterns = (
        r"\$\s?\d[\d,]*(?:\s*(?:-|–|to)\s*\$?\s?\d[\d,]*)?(?:\s*(?:/|per\s+)(?:month|year|week|event|day))?",
        r"\b\d[\d,]*\s*(?:-|–|to)\s*\d[\d,]*(?:\s*\+)?(?:\s*(?:%|hours?|hrs?|days?|weeks?|months?|years?|attendees|users|members|figures|units|orders|sales|buyers|artists|creators))?",
        r"\b\d[\d,]*\s*\+\s*(?:%|hours?|hrs?|days?|weeks?|months?|years?|attendees|users|members|figures|units|orders|sales|buyers|artists|creators)",
    )
    found = []
    for value in values:
        if not isinstance(value, str):
            continue
        for pattern in patterns:
            for match in re.finditer(pattern, value, flags=re.IGNORECASE):
                phrase = match.group(0).strip()
                digits = set(re.findall(r"\d[\d,]*", phrase))
                if digits and not digits.issubset(supplied_numbers) and phrase not in found:
                    found.append(phrase)
    return found


def generate_hypotheses(raw_goal: str, count: int = 3,
                        founder_context: str = "", previous_candidates: str = "") -> List[OpportunityHypothesis]:
    """Ask the configured model for several distinct, explicitly unvalidated hypotheses."""
    count = max(1, min(int(count), 8))
    focus_count = count if re.search(r"\b(only|exclusively)\b", raw_goal, flags=re.IGNORECASE) else (2 * count + 2) // 3
    adjacent_count = count - focus_count
    prompt = HYPOTHESIS_PROMPT.format(
            goal=raw_goal.replace("</goal>", "&lt;/goal&gt;"), count=count,
            focused_count=focus_count, adjacent_count=adjacent_count,
            founder_context=founder_context.strip() or "Not provided; do not assume founder skills or access.",
            previous_candidates=previous_candidates or "None; this is the first batch.",
    )

    def has_enough_candidates(value):
        if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
            return False
        seen_rows = set()
        for row in value["candidates"]:
            if not isinstance(row, dict):
                continue
            name = _clean_string(row.get("name"))
            user = _clean_string(row.get("target_user"))
            problem = _clean_string(row.get("problem"))
            jtbd = _clean_string(row.get("jtbd"))
            if name and user and problem and jtbd:
                seen_rows.add((user.casefold(), problem.casefold()))
        return len(seen_rows) >= count

    response = call_json(
        [{"role": "user", "content": prompt}],
        worker_model(), temperature=0.2, max_tokens=min(6000, 1600 + 1100 * count),
    )
    if not has_enough_candidates(response):
        recovery_prompt = (
            "Your previous response was missing valid JSON or did not contain enough distinct candidates. "
            "Return only one complete JSON object with exactly the requested number of candidates. "
            "Do not include analysis, reasoning, markdown, or <think> text.\n\n" + prompt
        )
        response = call_json(
            [{"role": "user", "content": recovery_prompt}],
            worker_model(), temperature=0, max_tokens=min(10000, 2500 + 1500 * count),
        )
    if not isinstance(response, dict) or response.get("error"):
        detail = response.get("error", "invalid response") if isinstance(response, dict) else "invalid response"
        raw_excerpt = response.get("raw", "")[:300] if isinstance(response, dict) else ""
        if raw_excerpt:
            detail = f"{detail}; final response began: {raw_excerpt!r}"
        raise RuntimeError(f"Could not generate opportunity hypotheses after a format-recovery retry: {detail}")

    raw_candidates = response.get("candidates")
    if not isinstance(raw_candidates, list):
        raise RuntimeError("The model response did not contain a candidates list.")

    hypotheses = []
    seen = set()
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        name = _clean_string(item.get("name"))
        target_user = _clean_string(item.get("target_user"))
        problem = _clean_string(item.get("problem"))
        jtbd = _clean_string(item.get("jtbd"))
        if not all((name, target_user, problem, jtbd)):
            continue
        key = (target_user.casefold(), problem.casefold())
        if key in seen:
            continue
        seen.add(key)
        assumptions = item.get("assumptions", [])
        if not isinstance(assumptions, list):
            assumptions = [str(assumptions)] if assumptions else []
        buyer = _clean_string(item.get("buyer"))
        why_software = _clean_string(item.get("why_software"))
        first_validation = _clean_string(item.get("first_validation"))
        numeric_fields = [name, target_user, problem, jtbd, buyer, why_software,
                          first_validation, *(str(x) for x in assumptions)]
        hypotheses.append(OpportunityHypothesis(
            name=name,
            target_user=target_user,
            problem=problem,
            jtbd=jtbd,
            buyer=buyer or "Buyer needs research",
            why_software=why_software or "Needs research",
            assumptions=[str(x) for x in assumptions if str(x).strip()],
            first_validation=first_validation or "Interview target users about their current workaround.",
            unverified_estimates=_unverified_numbers(
                numeric_fields, f"{raw_goal} {founder_context}"),
        ))
        if len(hypotheses) == count:
            break

    if len(hypotheses) < count:
        raise RuntimeError(f"The model returned only {len(hypotheses)} of {count} usable, distinct hypotheses.")
    return hypotheses


def research_hypothesis(raw_goal: str, hypothesis: OpportunityHypothesis,
                        max_research_rounds: int = 3) -> ResearchedOpportunity:
    """Run deep search and collect targeted GitHub/Product Hunt signals; skip paid verdict calls."""
    candidate_id = f"discovery-{uuid.uuid4().hex}"
    orchestrator = V2Orchestrator(hypothesis.to_spec(raw_goal), candidate_id=candidate_id)
    findings, analysis = conduct_research(hypothesis, max_rounds=max_research_rounds)
    collected = []
    for finding in findings:
        category = {
            "pain": "pain", "workaround": "pain", "payment": "payment",
            "alternatives": "competition", "counterevidence": "general", "market": "general",
        }.get(finding.angle, "general")
        excerpt = finding.page_excerpt or finding.snippet
        claim = Claim(
            text=f"{finding.title}: {excerpt[:900]}", source=finding.source,
            source_url=finding.url, source_class="claimed", age_days=None,
            independence_id=finding.url, confidence=0.55,
            candidate_id=candidate_id, category=category,
        )
        collected.append(claim)

    query_map = _derive_queries(hypothesis.to_spec(raw_goal))
    for name, method, angle in [
        ("github_pain", orchestrator._run_github_research, "pain"),
        ("github_competition", orchestrator._run_github_research, "competition"),
        ("producthunt_competition", orchestrator._run_ph_research, "competition"),
    ]:
        source_claims = method(query_map[angle], angle)
        collected.extend(source_claims)
        orchestrator._record_source_result(name, source_claims)
    for provider in sorted({finding.source for finding in findings}):
        provider_claims = [claim for claim in collected if claim.source == provider]
        if provider.startswith("SerpAPI"):
            provider_name = "serpapi_deep_research"
        elif provider.startswith("Firecrawl"):
            provider_name = "firecrawl_deep_research"
        else:
            provider_name = "web_search_deep_research"
        orchestrator._record_source_result(provider_name, provider_claims)
    if not findings:
        orchestrator._record_source_result("web_search_deep_research", [])
    orchestrator.tracker.add_many(collected)
    claims = orchestrator.tracker.for_candidate(candidate_id)
    aggregate = orchestrator.tracker.aggregate(candidate_id)
    gates = orchestrator.tracker.gate_status(candidate_id)
    decision = evaluate(orchestrator.tracker, candidate_id)
    return ResearchedOpportunity(
        hypothesis=hypothesis,
        candidate_id=candidate_id,
        claims=[claim.to_dict() for claim in claims],
        aggregate=aggregate.to_dict(),
        gates=gates.to_dict(),
        decision=decision.to_dict(),
        sources_run=list(orchestrator.sources_run),
        sources_skipped=list(orchestrator.sources_skipped),
        research_analysis=analysis.to_dict(),
        source_findings=[finding.__dict__ for finding in findings],
    )


def run_discovery(raw_goal: str, count: int = 3, founder_context: str = "",
                  max_candidates: int = 6, max_research_rounds: int = 3) -> DiscoveryReport:
    """Explore fresh batches until a sourced provisional lead appears or the cap is reached."""
    batch_size = max(1, min(int(count), 8))
    cap = max(batch_size, min(int(max_candidates), 12))
    results = []
    satisfied = False
    stop_reason = "Reached the candidate limit without a source-backed lead worth a user test."
    while len(results) < cap:
        batch_count = min(batch_size, cap - len(results))
        previous = "\n".join(
            f"- {item.hypothesis.name} ({item.hypothesis.target_user}; {item.hypothesis.problem}): "
            f"{item.research_analysis.get('bottom_line', 'No clear conclusion')[:220]}"
            for item in results
        )
        hypotheses = generate_hypotheses(raw_goal, count=batch_count,
            founder_context=founder_context, previous_candidates=previous)
        batch_results = [research_hypothesis(raw_goal, hypothesis, max_research_rounds)
                         for hypothesis in hypotheses]
        results.extend(batch_results)
        leads = [item for item in batch_results
                 if item.research_analysis.get("research_satisfied")
                 and item.research_analysis.get("decision") == "promising_for_user_test"]
        if leads:
            satisfied = True
            stop_reason = "A candidate has a source-backed provisional case for testing with real users."
            break
    return DiscoveryReport(raw_goal, results, satisfied, stop_reason)


def render_discovery_report(report: DiscoveryReport) -> str:
    lines = ["# SaaS Opportunity Discovery", "", f"**Starting area:** {report.raw_goal}", "",
             f"**Research status:** {'A provisional lead is ready for user testing.' if report.research_satisfied else 'No candidate met the source-backed stopping condition.'}",
             f"**Why research stopped:** {report.stop_reason}", "",
             "> These are research hypotheses, not validated businesses. Public web signals cannot prove that customers will pay.", ""]
    for index, result in enumerate(report.opportunities, 1):
        hypothesis = result.hypothesis
        lines.extend([
            "---", "", f"## {index}. {hypothesis.name}", "",
            f"**Potential user hypothesis:** {hypothesis.target_user}", "",
            f"**Problem hypothesis:** {hypothesis.problem}", "",
            f"**Job to be done:** {hypothesis.jtbd}", "",
            f"**Potential buyer hypothesis:** {hypothesis.buyer}", "",
            f"**Why software might help:** {hypothesis.why_software}", "",
        ])
        if hypothesis.unverified_estimates:
            lines.extend(["**Unverified figures generated in this hypothesis:** " +
                          ", ".join(hypothesis.unverified_estimates) +
                          ". Treat these as assumptions, not facts.", ""])
        analysis = result.research_analysis
        if analysis:
            lines.extend([f"**Analyst disposition:** {analysis.get('decision', 'inconclusive')}",
                          f"**Research satisfied:** {'yes' if analysis.get('research_satisfied') else 'no'} after {analysis.get('rounds', 0)} round(s)",
                          f"**Research assessment:** {analysis.get('bottom_line', '')}", ""])
            source_urls = {s.get("source_id"): s.get("url") for s in result.source_findings}
            for section, title in [
                ("pain_signals", "Pain evidence"),
                ("current_workarounds", "Current workarounds"),
                ("competition_and_pricing", "Alternatives and pricing"),
                ("counterevidence", "Evidence against this opportunity"),
            ]:
                items = analysis.get(section, [])
                if not items:
                    continue
                lines.extend([f"**{title}:**"])
                for item in items:
                    citations = []
                    for source_id in item.get("source_ids", []):
                        url = source_urls.get(source_id)
                        citations.append(f"[{source_id}]({url})" if url else source_id)
                    lines.append(f"- {item.get('finding', '')} ({', '.join(citations)})")
                lines.append("")
            payment = analysis.get("payment_evidence", {})
            if payment:
                payment_refs = []
                for source_id in payment.get("source_ids", []):
                    url = source_urls.get(source_id)
                    payment_refs.append(f"[{source_id}]({url})" if url else source_id)
                suffix = f" ({', '.join(payment_refs)})" if payment_refs else ""
                lines.extend([f"**Willingness-to-pay evidence ({payment.get('status', 'unknown')}):** {payment.get('finding', '')}{suffix}", ""])
            if analysis.get("unknowns"):
                lines.append("**Still unknown:**")
                lines.extend(f"- {unknown}" for unknown in analysis["unknowns"])
                lines.append("")
        if hypothesis.assumptions:
            lines.append("**Assumptions to check:**")
            lines.extend(f"- {assumption}" for assumption in hypothesis.assumptions)
            lines.append("")
        lines.extend([f"**Suggested first test:** {hypothesis.first_validation}", ""])
        lines.append(f"**Sources that returned evidence:** {', '.join(result.sources_run) if result.sources_run else 'None'}")
        if result.sources_skipped:
            lines.append(f"**Unavailable or empty sources:** {', '.join(result.sources_skipped)}")
        lines.append("")
    lines.extend(["---", "", "## How to use this research", "",
                  "A promising disposition means the public evidence justifies a small real user test; it does not establish market size, retention, or willingness to pay. If no candidate met that bar, treat this run as inconclusive and use the listed evidence gaps to guide the next searches."])
    return "\n".join(lines)
