"""Offline unit tests for the discovery-first workflow."""
import unittest
from unittest.mock import patch

from discovery import (
    DiscoveryReport,
    OpportunityHypothesis,
    ResearchedOpportunity,
    generate_hypotheses,
    render_discovery_report,
    run_discovery,
)
from evidence_tracker import Claim, EvidenceTracker
from deep_research import _normalize_analysis, SourceFinding
from minimax_client import parse_json


class DiscoveryTests(unittest.TestCase):
    def test_json_parser_accepts_fenced_json_with_leading_text(self):
        parsed = parse_json('Here is the result:\n```json\n{"candidates": []}\n```')
        self.assertEqual(parsed, {"candidates": []})

    def test_generates_distinct_hypotheses_without_user_spec(self):
        candidates = [
            {
                "name": "Applicant follow-up tracker",
                "target_user": "Independent recruiters placing nurses",
                "problem": "Recruiters lose track of candidate follow-ups",
                "jtbd": "When candidates go quiet, I want to follow up, so I can fill roles faster.",
                "assumptions": ["Recruiters use spreadsheets"],
            },
            {
                "name": "Job application organizer",
                "target_user": "Mid-career engineers changing jobs",
                "problem": "Applicants lose track of interview stages",
                "jtbd": "When I apply to roles, I want to track progress, so I can manage my search.",
                "assumptions": ["Applicants manage multiple applications"],
            },
        ]
        with patch("discovery.call_json", return_value={"candidates": candidates}):
            result = generate_hypotheses("SaaS for the job marketplace", count=2)
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0].target_user, result[1].target_user)

    def test_rejects_malformed_generation_response(self):
        with patch("discovery.call_json", return_value={"error": "invalid_json"}):
            with self.assertRaises(RuntimeError):
                generate_hypotheses("job marketplace", count=2)

    def test_report_labels_opportunities_as_unvalidated(self):
        hypothesis = OpportunityHypothesis(
            name="Application organizer",
            target_user="Mid-career engineers changing jobs",
            problem="Applications are hard to track",
            jtbd="When I apply, I want to track progress, so I can follow up.",
            buyer="Job seeker; payment willingness unknown",
            why_software="Could consolidate application status",
            success_signal="Hypothesis: 5 users complete a paid trial",
            assumptions=["Users will pay for organization"],
            first_validation="Interview five job seekers",
        )
        result = ResearchedOpportunity(
            hypothesis=hypothesis,
            candidate_id="candidate-test",
            claims=[],
            aggregate={"composite_score": 0, "total": 0, "verified": 0,
                       "claimed": 0, "heuristic": 0},
            gates={},
            decision={"verdict": "NEEDS_MORE_DATA", "blocking_reasons": []},
            sources_run=[],
            sources_skipped=[],
        )
        report = render_discovery_report(DiscoveryReport("job marketplace", [result]))
        self.assertIn("not validated businesses", report)
        self.assertIn("willingness to pay", report)
        self.assertIn("Interview five job seekers", report)

    def test_undated_search_claims_do_not_count_as_recent(self):
        tracker = EvidenceTracker()
        tracker.add(Claim(text="Undated result", source="search", source_class="claimed",
                           age_days=None, candidate_id="c"))
        aggregate = tracker.aggregate("c")
        self.assertEqual(aggregate.age_unknown, 1)
        self.assertEqual(aggregate.pct_recent, 0)

    def test_discovery_ranks_by_collected_public_signal(self):
        hypotheses = [
            OpportunityHypothesis("A", "Group A", "Problem A", "When A, I want B, so C.",
                                  "Unknown", "Unknown", "Hypothesis"),
            OpportunityHypothesis("B", "Group B", "Problem B", "When B, I want C, so D.",
                                  "Unknown", "Unknown", "Hypothesis"),
        ]
        weak = ResearchedOpportunity(hypotheses[0], "a", [],
                                     {"composite_score": 5, "verified": 0, "unique_sources": 1},
                                     {}, {}, [], [])
        strong = ResearchedOpportunity(hypotheses[1], "b", [],
                                       {"composite_score": 20, "verified": 1, "unique_sources": 2},
                                       {}, {}, [], [])
        with patch("discovery.generate_hypotheses", return_value=hypotheses), \
             patch("discovery.research_hypothesis", side_effect=[weak, strong]):
            result = run_discovery("broad goal", count=2)
        self.assertEqual([item.candidate_id for item in result.opportunities], ["b", "a"])

    def test_analysis_drops_unmatched_citations(self):
        sources = [SourceFinding("S1", "Review", "https://example.com", "Excerpt", "pain", "Search")]
        analysis = _normalize_analysis({
            "pain_signals": [{"finding": "Supported pain", "source_ids": ["S1"]},
                             {"finding": "Unsupported claim", "source_ids": ["S9"]}],
            "payment_evidence": {"status": "direct", "finding": "They pay", "source_ids": ["S9"]},
        }, sources)
        self.assertEqual(len(analysis.pain_signals), 1)
        self.assertEqual(analysis.payment_evidence["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
