"""
Layer 1 of v2: Socratic Goal Parser.

Turns a vague goal into a sharp GoalSpec. Refuses to proceed if the spec is too soft.
"""
import os
import sys
import json
import argparse
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Callable

try:
    from minimax_client import call_json, worker_model
except ImportError:
    call_json = None
    worker_model = None


@dataclass
class GoalSpec:
    raw_goal: str = ""
    target_user: str = ""
    jtbd: str = ""
    success_signal: str = ""
    constraints: List[str] = field(default_factory=list)
    anti_goals: List[str] = field(default_factory=list)
    founder_fit: List[str] = field(default_factory=list)
    is_complete: bool = False
    missing_fields: List[str] = field(default_factory=list)

    def to_json(self):
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, raw):
        data = json.loads(raw)
        return cls(**data)


@dataclass
class Question:
    key: str
    prompt: str
    example: str
    validator: Optional[Callable[[str], bool]] = None
    optional: bool = False


def _is_specific_user(s):
    s = s.lower().strip()
    if len(s) < 10:
        return False
    vague = ["anyone", "everyone", "small business", "people", "users", "businesses", "companies"]
    return not any(v in s for v in vague)


def _is_jtbd(s):
    s = s.lower().strip()
    return ("when " in s or "if " in s) and ("want to" in s or "need to" in s) and ("so " in s or "in order to" in s)


QUESTIONS = [
    Question("target_user",
             "Name a SPECIFIC role + context (with company size) who would buy this.",
             "-> 'Head of Operations at a 20-person digital agency' (NOT 'small businesses')",
             validator=_is_specific_user),
    Question("jtbd",
             "Fill in the JTBD: When [trigger], I want to [action], so I can [outcome].",
             "-> 'When a client pays an invoice late, I want to auto-send a reminder, so I can stop chasing payments manually.'",
             validator=_is_jtbd),
    Question("success_signal",
             "30 days after launch, what ONE measurable number tells you this is working?",
             "-> '20 paying users at $49/mo' (NOT 'make money' or 'get traction')"),
    Question("constraints",
             "Hard constraints (budget, timeline, tech stack, geography, vertical). Comma-separated. 'none' if no constraints.",
             "-> 'solo buildable in 3 weeks, must use Python, US/EU only'", optional=True),
    Question("anti_goals",
             "What kind of idea do you NOT want? Comma-separated.",
             "-> 'no marketplace, no AI wrapper, no consumer, no mobile app'", optional=True),
    Question("founder_fit",
             "Your unfair advantages: skills, network, domain knowledge. What can you do that 99% of founders can't?",
             "-> '10 years in pharma QA, can cold-call any hospital admin, built 3 side projects'", optional=True),
]


def validate(spec):
    missing = []
    if not spec.target_user or not _is_specific_user(spec.target_user):
        missing.append("target_user")
    if not spec.jtbd or not _is_jtbd(spec.jtbd):
        missing.append("jtbd")
    if not spec.success_signal or len(spec.success_signal.strip()) < 10:
        missing.append("success_signal")
    spec.missing_fields = missing
    spec.is_complete = len(missing) == 0
    return spec


def _ask_question(q):
    while True:
        print(f"\n{q.prompt}")
        print(f"   {q.example}")
        if q.optional:
            print("   (optional — press Enter to skip)")
        answer = input("   > ").strip()
        if not answer:
            if q.optional:
                return ""
            print("   [!] This one is required. Please answer.")
            continue
        if q.validator and not q.validator(answer):
            print(f"   [!] That answer is too vague. Look at the example and try again.")
            continue
        return answer


def interactive_loop(raw_goal):
    print(f"\n[goal] Raw goal: {raw_goal}")
    print("=" * 70)
    print("Let's sharpen this into a GoalSpec. Answer each question.")
    print("Vague answers will be rejected — that's on purpose.")
    print("=" * 70)
    spec = GoalSpec(raw_goal=raw_goal)
    for q in QUESTIONS:
        setattr(spec, q.key, _ask_question(q))
    if spec.constraints:
        spec.constraints = [s.strip() for s in spec.constraints.split(",") if s.strip() and s.lower() != "none"]
    if spec.anti_goals:
        spec.anti_goals = [s.strip() for s in spec.anti_goals.split(",") if s.strip()]
    if spec.founder_fit:
        spec.founder_fit = [s.strip() for s in spec.founder_fit.split(",") if s.strip()]
    return validate(spec)


AUTO_SPEC_PROMPT = """You are a sharp product strategist. The user has a vague goal.
Turn it into a precise GoalSpec.

USER'S RAW GOAL:
{goal}

OUTPUT JSON matching this schema exactly:
{{
  "target_user": "specific role + company size + context",
  "jtbd": "When [trigger], I want to [action], so I can [outcome]",
  "success_signal": "one measurable number for 30-day success",
  "constraints": ["list", "of", "hard", "limits"],
  "anti_goals": ["things to explicitly exclude"],
  "founder_fit": ["user's likely unfair advantages — be honest if you don't know"]
}}

RULES:
- target_user MUST be specific.
- jtbd MUST follow the When/I want to/so I can structure verbatim.
- success_signal MUST be a concrete number with a time window.
- Be honest about uncertainty — use [] for things you can't infer.

Return ONLY the JSON object, no commentary."""


def auto_fill(raw_goal):
    if call_json is None:
        print("[!] minimax_client not importable. Use interactive mode.")
        return None
    raw = call_json(
        [{"role": "user", "content": AUTO_SPEC_PROMPT.format(goal=raw_goal)}],
        worker_model(), max_tokens=400,
    )
    if not raw or raw.get("error"):
        print(f"   [!] Auto-fill failed: {raw}")
        return None
    return validate(GoalSpec(
        raw_goal=raw_goal,
        target_user=raw.get("target_user", ""),
        jtbd=raw.get("jtbd", ""),
        success_signal=raw.get("success_signal", ""),
        constraints=raw.get("constraints", []) or [],
        anti_goals=raw.get("anti_goals", []) or [],
        founder_fit=raw.get("founder_fit", []) or [],
    ))


def auto_mode(raw_goal):
    spec = auto_fill(raw_goal) or GoalSpec(raw_goal=raw_goal)
    print("\n[spec] Inferred GoalSpec (review/edit each field, press Enter to accept):")
    print("=" * 70)
    for q in QUESTIONS:
        current = getattr(spec, q.key)
        if isinstance(current, list):
            current_display = ", ".join(current)
        else:
            current_display = current or ""
        if current_display:
            print(f"\n{q.key}: {current_display}")
        else:
            print(f"\n{q.key}: (empty)")
        new_val = input(f"   Edit (Enter to {'skip' if q.optional else 'accept'}): ").strip()
        if new_val:
            if q.key in ("constraints", "anti_goals", "founder_fit"):
                setattr(spec, q.key, [s.strip() for s in new_val.split(",") if s.strip()])
            else:
                setattr(spec, q.key, new_val)
    return validate(spec)


def main():
    parser = argparse.ArgumentParser(description="Layer 1 of v2: Socratic Goal Parser.")
    parser.add_argument("goal", help="Your raw goal")
    parser.add_argument("--auto", action="store_true", help="Use M3 to auto-fill the spec, then confirm/edit.")
    parser.add_argument("--out", default="goal_spec.json")
    args = parser.parse_args()
    spec = auto_mode(args.goal) if args.auto else interactive_loop(args.goal)
    print("\n" + "=" * 70)
    print("[spec] FINAL GOALSPEC")
    print("=" * 70)
    print(spec.to_json())
    if not spec.is_complete:
        print(f"\n[!] Spec is INCOMPLETE — missing: {', '.join(spec.missing_fields)}")
        sys.exit(1)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(spec.to_json())
    print(f"\n[ok] Spec complete. Saved to: {args.out}")


if __name__ == "__main__":
    main()