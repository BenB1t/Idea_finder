"""Discovery-first CLI for exploring and validating SaaS opportunities."""
import argparse
import os
import sys
import uuid

from discovery import render_discovery_report, run_discovery
from dossier_renderer import render_dossier
from goal_parser import GoalSpec, auto_mode, interactive_loop
from iterate import IterativeOrchestrator
from v2_orchestrator import V2Orchestrator


def get_spec(args) -> GoalSpec:
    """Load a saved GoalSpec or use the legacy guided questionnaire."""
    if args.spec:
        with open(args.spec, encoding="utf-8") as f:
            spec = GoalSpec.from_json(f.read())
        if not spec.is_complete:
            raise ValueError(f"Spec from {args.spec} is incomplete: {spec.missing_fields}")
        return spec

    spec = auto_mode(args.goal) if args.auto_spec else interactive_loop(args.goal)
    if not spec.is_complete:
        raise ValueError(f"Spec is incomplete: {', '.join(spec.missing_fields)}")
    return spec


def main():
    parser = argparse.ArgumentParser(
        description="Generate and research SaaS opportunity hypotheses from a broad goal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python find_idea.py "Find a SaaS opportunity in the job marketplace"
  python find_idea.py "..." --candidates 5
  python find_idea.py "..." --guided
  python find_idea.py --spec goal_spec.json
""",
    )
    parser.add_argument("goal", nargs="?", help="Broad area to explore")
    parser.add_argument("--candidates", type=int, default=3,
                        help="Hypotheses per exploration batch (2-8, default 3)")
    parser.add_argument("--max-candidates", type=int, default=6,
                        help="Maximum distinct hypotheses before reporting inconclusive (default 6)")
    parser.add_argument("--research-rounds", type=int, default=3,
                        help="Targeted search rounds per hypothesis (default 3)")
    parser.add_argument("--founder-context", default="",
                        help="Optional skills, domain knowledge, budget, or audiences you can reach")
    parser.add_argument("--guided", action="store_true",
                        help="Use the original questionnaire for one preselected idea")
    parser.add_argument("--spec", help="Load a saved GoalSpec and run the guided pipeline")
    parser.add_argument("--auto-spec", action="store_true",
                        help="In guided mode, draft a GoalSpec with MiniMax, then review it")
    parser.add_argument("--no-iterate", action="store_true",
                        help="In guided mode, run one research pass")
    parser.add_argument("--max-iterations", type=int, default=3,
                        help="Maximum guided research iterations when data is insufficient")
    parser.add_argument("--out-dir", default="find_idea_runs",
                        help="Directory for Markdown reports")
    args = parser.parse_args()

    guided = bool(args.guided or args.spec or args.auto_spec)
    if not args.goal and not args.spec:
        parser.error("provide a broad goal, or use --spec")
    if args.goal is not None and not args.goal.strip():
        parser.error("goal cannot be empty")
    if not 2 <= args.candidates <= 8:
        parser.error("--candidates must be between 2 and 8")
    if not 2 <= args.max_candidates <= 12:
        parser.error("--max-candidates must be between 2 and 12")
    if args.max_candidates < args.candidates:
        parser.error("--max-candidates must be at least --candidates")
    if not 1 <= args.research_rounds <= 5:
        parser.error("--research-rounds must be between 1 and 5")
    if args.spec and (args.guided or args.auto_spec):
        parser.error("--spec cannot be combined with --guided or --auto-spec")

    label = args.goal or args.spec
    print("=" * 70)
    print("SAAS OPPORTUNITY FINDER")
    print("=" * 70)
    print(f"Starting area: {label}\n")

    try:
        if guided:
            spec = get_spec(args)
            candidate_id = f"candidate-{uuid.uuid4().hex}"
            if args.no_iterate:
                result = V2Orchestrator(spec, candidate_id=candidate_id).run()
            else:
                result = IterativeOrchestrator(
                    spec, candidate_id=candidate_id,
                    max_iterations=args.max_iterations,
                ).run()
            report_text = render_dossier(result)
            filename = f"{candidate_id}.md"
            summary = (f"Claims: {result.claims_collected} | Iterations: {result.iterations} | "
                       f"Verdict: {result.combined_verdict.final_verdict if result.combined_verdict else 'unknown'}")
        else:
            print(f"Exploring up to {args.max_candidates} hypotheses in batches of {args.candidates}...\n")
            report = run_discovery(
                args.goal, count=args.candidates,
                founder_context=args.founder_context,
                max_candidates=args.max_candidates,
                max_research_rounds=args.research_rounds,
            )
            report_text = render_discovery_report(report)
            filename = f"discovery-{uuid.uuid4().hex}.md"
            summary = (f"Hypotheses researched: {len(report.opportunities)} | "
                       f"Research satisfied: {'yes' if report.research_satisfied else 'no'}")

        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out_dir)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, filename)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report_text)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except Exception as e:
        print(f"\nCould not complete the discovery run: {e}", file=sys.stderr)
        return 1

    print(report_text)
    print(f"\nReport saved: {out_path}")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
