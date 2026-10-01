"""
Auto-iteration layer: when the orchestrator returns NEEDS_MORE_DATA,
try broader research angles before giving up.
"""
import time
from typing import Optional

from v2_orchestrator import V2Orchestrator, OrchestratorResult
from adversarial_verifier import CombinedVerdict
from gate_decision import evaluate
from evidence_tracker import AggregateEvidence, GateStatus


class IterativeOrchestrator(V2Orchestrator):
    """V2Orchestrator that auto-iterates on NEEDS_MORE_DATA."""

    def __init__(self, *args, max_iterations: int = 3, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_iterations = max(1, max_iterations)

    def run(self) -> OrchestratorResult:
        start = time.time()
        print("=" * 70)
        print(f"🚀 V2 ITERATIVE ORCHESTRATOR (max {self.max_iterations} iterations)")
        print(f"   Goal: {self.spec.raw_goal or self.spec.jtbd[:60]}")
        print("=" * 70)

        print(f"\n📍 Iteration 1/{self.max_iterations}")
        result = self._full_pass(start, iteration=1)
        iterations_used = 1

        for i in range(1, self.max_iterations):
            if not self._is_needs_more_data(result.combined_verdict):
                break

            print(f"\n📍 Iteration {i+1}/{self.max_iterations} — verdict was NEEDS_MORE_DATA, expanding...")
            added = self.expand_research()
            print(f"   ➕ Added {added} new claims")

            if added == 0:
                print(f"   ⚠️ No new evidence found. Stopping iteration.")
                break

            iterations_used = i + 1
            result = self._reevaluate_only(start, iteration=iterations_used)

            agg = self.tracker.aggregate(self.candidate_id)
            print(f"   📊 After iteration {iterations_used}: "
                  f"{agg.verified}V/{agg.claimed}C/{agg.heuristic}H, "
                  f"composite {agg.composite_score}/100, "
                  f"verdict={result.combined_verdict.final_verdict}")

        print(f"\n🏁 Final result after {iterations_used} iteration(s)")
        return result

    def _full_pass(self, start_time: float, iteration: int) -> OrchestratorResult:
        self.research()
        verdict = self.evaluate()
        return self._build_result(start_time, verdict, iteration)

    def _reevaluate_only(self, start_time: float, iteration: int) -> OrchestratorResult:
        verdict = self.evaluate()
        return self._build_result(start_time, verdict, iteration)

    def _build_result(self, start_time: float, verdict: CombinedVerdict, iteration: int) -> OrchestratorResult:
        agg = self.tracker.aggregate(self.candidate_id)
        gates = self.tracker.gate_status(self.candidate_id)
        return OrchestratorResult(
            goal_spec=self.spec,
            candidate_id=self.candidate_id,
            claims_collected=agg.total,
            sources_run=list(self.sources_run),
            sources_skipped=list(self.sources_skipped),
            combined_verdict=verdict,
            elapsed_seconds=round(time.time() - start_time, 2),
            iterations=iteration,
            tracker_summary={"aggregate": agg.to_dict(), "gates": gates.to_dict()},
        )

    @staticmethod
    def _is_needs_more_data(verdict: Optional[CombinedVerdict]) -> bool:
        return verdict is not None and verdict.final_verdict == "NEEDS_MORE_DATA"


def main():
    from goal_parser import GoalSpec
    from evidence_tracker import Claim, claim_from_serpapi_paa

    spec = GoalSpec(
        raw_goal="invoice chasing tool",
        target_user="Head of Operations at a 20-person digital agency",
        jtbd="When a client pays an invoice late, I want to auto-send a reminder, so I can stop chasing payments manually.",
        success_signal="20 paying users at $49/mo within 30 days",
        is_complete=True,
    )
    orch = IterativeOrchestrator(spec, candidate_id="iteration_demo", max_iterations=3)
    for i, t in enumerate(["Manual invoice reminders", "Wish Stripe had trigger"]):
        orch.tracker.add(Claim(text=f"Pain: {t}", source="github_issue", source_class="verified",
                                age_days=30, category="pain", confidence=0.85,
                                independence_id=f"gh_{i}", candidate_id=orch.candidate_id))
    result = orch.run()
    print(f"\n   Final: {result.combined_verdict.final_verdict} after {result.iterations} iteration(s)")


if __name__ == "__main__":
    main()