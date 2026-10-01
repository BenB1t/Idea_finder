"""
Layer 5 of v2: Multi-source orchestrator.

The main loop. Takes a GoalSpec, runs research across multiple sources in parallel,
collects Claims, runs gate-decision + adversarial verifier, returns a structured
dossier-ready Result.
"""
import os
import re
import sys
import json
import time
from typing import List, Dict, Optional
from dataclasses import dataclass, field, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from goal_parser import GoalSpec
from evidence_tracker import (
    EvidenceTracker, Claim,
    claim_from_github_issue, claim_from_g2_review, claim_from_ph_launch,
    claim_from_serpapi_paa, claim_from_reddit_thread,
    _age_days_from_iso,
)
from gate_decision import evaluate, Decision
from adversarial_verifier import (
    verify, combine, CombinedVerdict, VerifierVerdict, evaluate_with_verifier, format_combined,
)


@dataclass
class OrchestratorResult:
    """The full dossier-ready output of one v2 run."""
    goal_spec: GoalSpec
    candidate_id: str
    claims_collected: int = 0
    sources_run: List[str] = field(default_factory=list)
    sources_skipped: List[str] = field(default_factory=list)
    combined_verdict: Optional[CombinedVerdict] = None
    elapsed_seconds: float = 0.0
    iterations: int = 1
    tracker_summary: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "goal_spec": asdict(self.goal_spec),
            "candidate_id": self.candidate_id,
            "claims_collected": self.claims_collected,
            "sources_run": self.sources_run,
            "sources_skipped": self.sources_skipped,
            "combined_verdict": self.combined_verdict.to_dict() if self.combined_verdict else None,
            "elapsed_seconds": self.elapsed_seconds,
            "iterations": self.iterations,
            "tracker_summary": self.tracker_summary,
        }


def _detect_sources() -> Dict[str, bool]:
    available = {}
    available["serpapi"] = bool(os.getenv("SERPAPI_KEY"))
    # Firecrawl supports keyless API access; FIRECRAWL_API_KEY is optional.
    available["firecrawl"] = True
    available["github"] = bool(os.getenv("GITHUB_TOKEN"))
    available["producthunt"] = bool(os.getenv("PH_TOKEN"))
    return available


def _extract_jtbd_components(jtbd: str) -> Dict[str, str]:
    out = {"trigger": "", "action": "", "outcome": ""}
    s = jtbd.strip()
    m = re.search(r"(?:when|if)\s+(.+?),\s*I\s+(?:want|need)\s+to\s+", s, re.IGNORECASE)
    if m:
        out["trigger"] = m.group(1).strip()
    m = re.search(r"(?:want|need)\s+to\s+(.+?),\s*so\s+(?:I\s+can\s+)?", s, re.IGNORECASE)
    if m:
        out["action"] = m.group(1).strip()
    m = re.search(r"(?:so\s+(?:I\s+can|in\s+order\s+to)|so\s+)(.+?)(?:\.|$)", s, re.IGNORECASE)
    if m:
        out["outcome"] = m.group(1).strip()
    return out


def _derive_queries(spec: GoalSpec) -> Dict[str, List[str]]:
    jtbd_parts = _extract_jtbd_components(spec.jtbd)
    action = jtbd_parts["action"] or spec.jtbd
    trigger = jtbd_parts["trigger"] or ""
    user = spec.target_user

    return {
        "pain": [
            f'{user} {action}',
            f'{action} frustration',
            f'{action} manual pain',
        ],
        "payment": [
            f'{action} pricing',
            f'{action} software cost',
            f'{action} subscription',
        ],
        "jtbd": [
            f'how to {action}',
            f'best tool for {action}',
            f'{action} automation',
        ],
        "competition": [
            f'{action} software',
            f'{action} alternatives',
            f'{action} app',
        ],
    }


class V2Orchestrator:
    """Runs the full v2 pipeline against one GoalSpec."""

    def __init__(self, goal_spec: GoalSpec, candidate_id: Optional[str] = None):
        self.spec = goal_spec
        self.candidate_id = candidate_id or f"candidate-{int(time.time())}"
        self.tracker = EvidenceTracker()
        self.sources_available = _detect_sources()
        self.sources_run: List[str] = []
        self.sources_skipped: List[str] = []
        self._claims_added: List[Claim] = []

    def _run_serpapi_research(self, queries: List[str], angle: str) -> List[Claim]:
        if not self.sources_available.get("serpapi"):
            return []
        try:
            from sources.serpapi import google_research
        except ImportError:
            return []
        claims = []
        for q in queries[:2]:
            try:
                search_data = google_research(q, num=5)
                for item in search_data["organic_results"]:
                    if item.get("url"):
                        claims.append(Claim(
                            text=f"Search result for '{q}': {item.get('title', '')}. {item.get('snippet', '')}",
                            source="google_search_result",
                            source_url=item["url"],
                            source_class="claimed",
                            age_days=None,
                            independence_id=f"serp_{item['url']}",
                            confidence=0.5,
                            candidate_id=self.candidate_id,
                            category=angle,
                        ))
                for question in search_data["questions"][:5]:
                    claims.append(claim_from_serpapi_paa(question, candidate_id=self.candidate_id, category=angle))
                for related in search_data["related_searches"][:5]:
                    claims.append(Claim(
                        text=f"Related search interest: '{related}'",
                        source="google_related",
                        source_class="heuristic",
                        age_days=None,
                        confidence=0.5,
                        candidate_id=self.candidate_id,
                        category=angle,
                    ))
            except Exception as e:
                print(f"   ⚠️ SerpAPI '{q}' failed: {e}")
                continue
        return claims

    def _run_firecrawl_research(self, queries: List[str], angle: str) -> List[Claim]:
        """Collect source-backed web evidence with Firecrawl Search and page text."""
        if not self.sources_available.get("firecrawl"):
            return []
        try:
            from sources.firecrawl import search_web
        except ImportError:
            return []
        claims = []
        for query in queries[:1]:
            try:
                for result in search_web(query, limit=3):
                    url = result.get("url", "")
                    if not url:
                        continue
                    excerpt = result.get("markdown") or result.get("snippet") or ""
                    title = result.get("title") or url
                    claims.append(Claim(
                        text=f"{title}: {excerpt[:900]}" if excerpt else f"Search result: {title}",
                        source="firecrawl_search",
                        source_url=url,
                        source_class="claimed",
                        age_days=None,
                        confidence=0.6,
                        candidate_id=self.candidate_id,
                        category=angle,
                        independence_id=f"firecrawl_{url}",
                    ))
            except Exception as e:
                print(f"   ⚠️ Firecrawl '{query}' failed: {e}")
        return claims

    def _run_github_research(self, queries: List[str], angle: str) -> List[Claim]:
        if not self.sources_available.get("github"):
            return []
        try:
            from sources.github import adjacent_tools, get_recent_issues, get_repo
        except ImportError:
            return []
        claims = []
        for q in queries[:2]:
            try:
                tools = adjacent_tools(q, top_n=5)
                for t in tools:
                    claims.append(Claim(
                        text=f"Adjacent OSS tool: {t['full_name']} ({t['stars']}⭐) — {t.get('description', '')[:80]}",
                        source="github_repo",
                        source_url=t.get("url"),
                        source_class="verified",
                        age_days=_age_days_from_iso(t.get("last_push")),
                        confidence=0.8,
                        candidate_id=self.candidate_id,
                        category="competition",
                        independence_id=f"github_repo_{t['full_name']}",
                    ))
                if angle == "pain" and tools:
                    top = tools[0]
                    owner, _, repo = top["full_name"].partition("/")
                    issues = get_recent_issues(owner, repo, days=180)[:5]
                    for issue in issues:
                        claims.append(claim_from_github_issue(issue, candidate_id=self.candidate_id))
            except Exception as e:
                print(f"   ⚠️ GitHub '{q}' failed: {e}")
                continue
        return claims

    def _run_ph_research(self, queries: List[str], angle: str) -> List[Claim]:
        if not self.sources_available.get("producthunt"):
            return []
        try:
            from sources.producthunt import search_launches
        except ImportError:
            return []
        claims = []
        for q in queries[:2]:
            try:
                launches = search_launches(q, limit=10)
                for launch in launches[:5]:
                    claims.append(claim_from_ph_launch(launch, candidate_id=self.candidate_id))
            except Exception as e:
                print(f"   ⚠️ PH '{q}' failed: {e}")
                continue
        return claims

    def research(self) -> List[Claim]:
        queries = _derive_queries(self.spec)
        print(f"\n📡 Research phase: 4 angles, {sum(len(q) for q in queries.values())} total queries")
        tasks = []
        for angle, qs in queries.items():
            tasks.append((f"firecrawl_{angle}", self._run_firecrawl_research, qs, angle))
            if angle in ("pain",):
                tasks.append((f"github_{angle}", self._run_github_research, qs, angle))
                tasks.append((f"serpapi_{angle}", self._run_serpapi_research, qs, angle))
            elif angle == "payment":
                tasks.append((f"serpapi_{angle}", self._run_serpapi_research, qs, angle))
            elif angle == "jtbd":
                tasks.append((f"serpapi_{angle}", self._run_serpapi_research, qs, angle))
            elif angle == "competition":
                tasks.append((f"github_{angle}", self._run_github_research, qs, angle))
                tasks.append((f"ph_{angle}", self._run_ph_research, qs, angle))
        all_claims = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(fn, qs, angle): name for name, fn, qs, angle in tasks}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result(timeout=60)
                    all_claims.extend(result)
                    self._record_source_result(name, result)
                except Exception as e:
                    print(f"   ⚠️ {name} errored: {e}")
                    self.sources_skipped.append(f"{name} (error)")

        for claim in all_claims:
            self.tracker.add(claim)
            if claim not in self._claims_added:
                self._claims_added.append(claim)

        print(f"\n   ✅ Collected {len(all_claims)} claims from {len(self.sources_run)} sources")
        if self.sources_skipped:
            print(f"   ⏭️  Skipped: {', '.join(self.sources_skipped)}")
        return all_claims

    def evaluate(self) -> CombinedVerdict:
        print(f"\n⚖️ Evaluation phase:")
        candidate_summary = {
            "name": f"[auto-generated from: {self.spec.jtbd[:50]}...]",
            "target_user": self.spec.target_user,
            "jtbd": self.spec.jtbd,
            "success_signal": self.spec.success_signal,
        }
        return evaluate_with_verifier(self.candidate_id, candidate_summary, self.spec, self.tracker)

    def _record_source_result(self, name: str, claims: List[Claim]) -> None:
        """Record task outcomes on the coordinator thread for accurate reporting."""
        if claims:
            if name not in self.sources_run:
                self.sources_run.append(name)
        else:
            skipped = f"{name} (no results or unavailable)"
            if skipped not in self.sources_skipped:
                self.sources_skipped.append(skipped)

    def expand_research(self) -> int:
        queries = _derive_queries(self.spec)
        initial_claim_ids = set(self.tracker.claims)
        print(f"\n   🔍 Trying broader research angles...")
        for angle, qs in queries.items():
            serp_name = f"serpapi_{angle}"
            serp_claims = self._run_serpapi_research(qs, angle)
            self._record_source_result(serp_name, serp_claims)
            for c in serp_claims:
                self.tracker.add(c)
            ph_name = f"ph_{angle}"
            ph_claims = self._run_ph_research(qs, angle)
            self._record_source_result(ph_name, ph_claims)
            for c in ph_claims:
                self.tracker.add(c)
            github_name = f"github_{angle}"
            github_claims = self._run_github_research(qs, angle)
            self._record_source_result(github_name, github_claims)
            for c in github_claims:
                self.tracker.add(c)

        spec_claims = [
            Claim(text=f"User-stated JTBD: {self.spec.jtbd}", source="user_input",
                  source_class="verified", age_days=0, confidence=0.7,
                  candidate_id=self.candidate_id, category="jtbd", independence_id="user_jtbd"),
            Claim(text=f"User-stated target user: {self.spec.target_user}", source="user_input",
                  source_class="verified", age_days=0, confidence=0.7,
                  candidate_id=self.candidate_id, category="jtbd", independence_id="user_target"),
        ]
        if self.spec.constraints:
            spec_claims.append(Claim(
                text=f"User-stated constraints: {', '.join(self.spec.constraints)}",
                source="user_input", source_class="verified", age_days=0, confidence=0.8,
                candidate_id=self.candidate_id, category="general", independence_id="user_constraints",
            ))
        for c in spec_claims:
            self.tracker.add(c)
        return len(set(self.tracker.claims) - initial_claim_ids)

    def run(self) -> OrchestratorResult:
        start = time.time()
        print("=" * 70)
        print(f"🚀 V2 ORCHESTRATOR")
        print(f"   Goal: {self.spec.raw_goal or self.spec.jtbd[:60]}")
        print(f"   Target user: {self.spec.target_user}")
        print(f"   JTBD: {self.spec.jtbd[:80]}{'...' if len(self.spec.jtbd) > 80 else ''}")
        print("=" * 70)

        self.research()
        verdict = self.evaluate()

        elapsed = time.time() - start
        agg = self.tracker.aggregate(self.candidate_id)
        gates = self.tracker.gate_status(self.candidate_id)

        result = OrchestratorResult(
            goal_spec=self.spec,
            candidate_id=self.candidate_id,
            claims_collected=agg.total,
            sources_run=self.sources_run,
            sources_skipped=self.sources_skipped,
            combined_verdict=verdict,
            elapsed_seconds=round(elapsed, 2),
            iterations=1,
            tracker_summary={"aggregate": agg.to_dict(), "gates": gates.to_dict()},
        )

        print(f"\n📋 RESULT")
        print("=" * 70)
        print(format_combined(verdict))
        print(f"\n   Claims: {result.claims_collected} total "
              f"({agg.verified}V / {agg.claimed}C / {agg.heuristic}H), "
              f"composite {agg.composite_score}/100")
        print(f"   Sources run: {len(result.sources_run)}, skipped: {len(result.sources_skipped)}")
        print(f"   Elapsed: {result.elapsed_seconds}s")
        return result


def main():
    spec = GoalSpec(
        raw_goal="invoice chasing tool",
        target_user="Head of Operations at a 20-person digital agency",
        jtbd="When a client pays an invoice late, I want to auto-send a reminder, so I can stop chasing payments manually.",
        success_signal="20 paying users at $49/mo within 30 days",
        constraints=["solo buildable in 3 weeks", "uses Stripe"],
        anti_goals=["no marketplace", "no AI wrapper"],
        founder_fit=["10 years in agency finance", "prior Stripe integration experience"],
        is_complete=True,
    )
    orch = V2Orchestrator(spec, candidate_id="invoice_chaser_demo")
    result = orch.run()
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f"idea_v2_runs/{result.candidate_id}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2)
    print(f"\n💾 Dossier saved to: {out_path}")


if __name__ == "__main__":
    main()
