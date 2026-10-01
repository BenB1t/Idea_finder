"""
Smoke tests for v2 layers 1-5: GoalParser, EvidenceTracker, GateDecision, AdversarialVerifier, Orchestrator.
Run:  python test_v2_layers.py
"""
import sys

print("=" * 70)
print("LAYER 4 — Adversarial Verifier (combine logic)")
print("=" * 70)

from adversarial_verifier import combine, VerifierVerdict, COMBINED_VERDICTS, format_combined


def fake_gate(verdict, conf=0.9):
    from gate_decision import Decision
    return Decision(candidate_id='test', verdict=verdict, confidence=conf, summary=f'gate {verdict}')


def fake_verifier(passed, concerns=None, gates=None, question=''):
    return VerifierVerdict(
        candidate_id='test', passed=passed, confidence=0.8 if passed else 0.6,
        failing_gates=gates or [], key_concerns=concerns or [],
        would_tell_founder='test', blocking_question=question, model_used='M2.7-mock',
    )


cases = [
    ('GO', True, 'CONFIRMED_GO'), ('GO', False, 'CONFLICT'),
    ('CONDITIONAL_GO', True, 'CONDITIONAL_GO'), ('CONDITIONAL_GO', False, 'NO_GO'),
    ('NO_GO', True, 'NO_GO'), ('NO_GO', False, 'NO_GO'),
    ('NEEDS_MORE_DATA', None, 'NEEDS_MORE_DATA'),
]
for gate_v, passed, expected in cases:
    gd = fake_gate(gate_v)
    v = None if passed is None else fake_verifier(passed)
    cv = combine(gd, verifier=v)
    assert cv.final_verdict == expected, f'{gate_v} + {passed}: expected {expected}, got {cv.final_verdict}'
print(f'  [ok] all 7 combine paths return correct final_verdict')

cv = combine(fake_gate('GO', 0.95), fake_verifier(True, concerns=['market saturation risk']))
output = format_combined(cv)
assert 'CONFIRMED_GO' in output and 'saturation risk' in output
print(f'  [ok] format_combined renders verdict + concerns')

assert set(COMBINED_VERDICTS) == {'CONFIRMED_GO', 'CONDITIONAL_GO', 'CONFLICT', 'NO_GO', 'NEEDS_MORE_DATA'}
print(f'  [ok] COMBINED_VERDICTS has all 5 expected values')

print()
print("=" * 70)
print("LAYER 3 — Gate Decision")
print("=" * 70)

from evidence_tracker import EvidenceTracker, Claim
from gate_decision import evaluate, format_decision, VERDICTS

t = EvidenceTracker()
cid = 'go_candidate'
for i in range(6):
    t.add(Claim(text=f'github pain {i}', source='github_issue', source_class='verified',
                age_days=30, category='pain', confidence=0.85, independence_id=f'gh_{i}', candidate_id=cid))
t.add(Claim(text='G2 review', source='g2_review', source_class='verified',
            age_days=14, category='payment', confidence=0.9, independence_id='g2_1', candidate_id=cid))
t.add(Claim(text='PH launch A', source='ph_launch', source_class='claimed',
            age_days=60, category='competition', confidence=0.7, independence_id='ph_A', candidate_id=cid))
t.add(Claim(text='PAA', source='google_paa', source_class='heuristic',
            age_days=0, category='jtbd', confidence=0.6, independence_id='paa_1', candidate_id=cid))
d = evaluate(t, cid)
assert d.verdict == 'GO'
print(f'  [ok] GO path: {d.verdict} (confidence {d.confidence})')

t2 = EvidenceTracker()
cid = 'thin'
for i in range(5):
    t2.add(Claim(text=f'reddit pain {i}', source='reddit', source_class='claimed',
                 age_days=30, category='pain', confidence=0.5, independence_id=f'reddit_{i}', candidate_id=cid))
d2 = evaluate(t2, cid)
assert d2.verdict == 'NEEDS_MORE_DATA'
print(f'  [ok] NEEDS_MORE_DATA path: {d2.verdict} ({len(d2.next_steps)} next steps)')

t2b = EvidenceTracker()
cid = 'no_go'
for i in range(2):
    t2b.add(Claim(text=f'github pain {i}', source='github_issue', source_class='verified',
                  age_days=30, category='pain', confidence=0.85, independence_id=f'gh_{i}', candidate_id=cid))
for i in range(3):
    t2b.add(Claim(text=f'reddit pain {i}', source='reddit', source_class='claimed',
                  age_days=30, category='pain', confidence=0.7, independence_id=f'reddit_{i}', candidate_id=cid))
t2b.add(Claim(text='PH launch', source='ph_launch', source_class='claimed',
               age_days=60, category='competition', confidence=0.7, independence_id='ph_1', candidate_id=cid))
t2b.add(Claim(text='PAA', source='google_paa', source_class='heuristic',
               age_days=0, category='jtbd', confidence=0.6, independence_id='paa_1', candidate_id=cid))
d2b = evaluate(t2b, cid)
assert d2b.verdict == 'NO_GO' and len(d2b.blocking_reasons) >= 2
print(f'  [ok] NO_GO path: {d2b.verdict} ({len(d2b.blocking_reasons)} blocking reasons)')

t4 = EvidenceTracker()
cid = 'conditional'
for i in range(3):
    t4.add(Claim(text=f'pain {i}', source='github_issue', source_class='verified',
                 age_days=30, category='pain', confidence=0.85, independence_id=f'gh_{i}', candidate_id=cid))
t4.add(Claim(text='payment', source='g2_review', source_class='verified',
             age_days=14, category='payment', confidence=0.9, independence_id='g2', candidate_id=cid))
d4 = evaluate(t4, cid)
assert d4.verdict == 'CONDITIONAL_GO' and len(d4.caveats) > 0
print(f'  [ok] CONDITIONAL_GO path: {d4.verdict} ({len(d4.caveats)} caveats)')

assert evaluate(EvidenceTracker(), 'empty').verdict == 'NEEDS_MORE_DATA'
print(f'  [ok] empty tracker: NEEDS_MORE_DATA')

import json
json.dumps(d.to_dict())
print(f'  [ok] JSON serializable')

assert d.aggregate is not None and d.gate_status is not None
print(f'  [ok] audit trail included')

print()
print("=" * 70)
print("LAYER 2 — Evidence Tracker")
print("=" * 70)

tt = EvidenceTracker()
tt.add(Claim(text='test', source='x'))
tt.add(Claim(text='test', source='x'))
assert len(tt.claims) == 1
print(f'  [ok] dedup by content hash')

fresh_v = Claim(text='x', source='y', source_class='verified', age_days=0, confidence=1.0)
fresh_h = Claim(text='x', source='y', source_class='heuristic', age_days=0, confidence=1.0)
old_c = Claim(text='x', source='y', source_class='claimed', age_days=365, confidence=1.0)
assert fresh_v.weight > fresh_h.weight > old_c.weight
print(f'  [ok] weight ordering: V({fresh_v.weight:.2f}) > H({fresh_h.weight:.2f}) > old_C({old_c.weight:.2f})')

te = EvidenceTracker()
cid = 'echo'
for i in range(5):
    te.add(Claim(text='echoed pain', source='reddit', source_class='claimed',
                 candidate_id=cid, independence_id='same_signal', category='pain'))
assert te.aggregate(cid).independent_signals == 1
print(f'  [ok] echo dedup: 5 echoed claims -> 1 independent signal')

tr = EvidenceTracker()
cid = 'mixed'
tr.add(Claim(text='fresh', source='x', source_class='verified', age_days=10, candidate_id=cid, category='pain'))
tr.add(Claim(text='stale', source='x', source_class='verified', age_days=730, candidate_id=cid, category='pain'))
agg = tr.aggregate(cid)
assert agg.avg_age_days == 370.0 and agg.pct_recent == 50.0
print(f'  [ok] recency decay: 50% recent computed correctly')

print()
print("=" * 70)
print("LAYER 1 — Goal Parser")
print("=" * 70)

from goal_parser import GoalSpec, validate

spec = GoalSpec(raw_goal='test', target_user='small businesses',
                jtbd='When invoice late, want to remind, so get paid',
                success_signal='20 paying users in 30 days')
spec = validate(spec)
assert not spec.is_complete and 'target_user' in spec.missing_fields
print(f'  [ok] rejects vague target_user')

spec2 = GoalSpec(raw_goal='test', target_user='Head of Ops at a 20-person digital agency',
                 jtbd='When invoice late, want to remind, so get paid',
                 success_signal='20 paying users in 30 days')
spec2 = validate(spec2)
assert spec2.is_complete
print(f'  [ok] accepts specific target_user')

spec3 = GoalSpec(raw_goal='test', target_user='Head of Ops at a 20-person digital agency',
                 jtbd='help me send invoices', success_signal='20 paying users in 30 days')
spec3 = validate(spec3)
assert not spec3.is_complete and 'jtbd' in spec3.missing_fields
print(f'  [ok] rejects malformed JTBD')

spec4 = GoalSpec(raw_goal='test', target_user='Head of Ops at 20-person agency',
                 jtbd='When invoice late, want to remind, so get paid',
                 success_signal='20 paying users',
                 constraints=['solo', '3 weeks'], anti_goals=['no marketplace'],
                 founder_fit=['10 years in finance'])
spec4 = validate(spec4)
roundtrip = GoalSpec.from_json(spec4.to_json())
assert roundtrip.target_user == spec4.target_user and roundtrip.constraints == spec4.constraints
print(f'  [ok] JSON round-trip works')

print()
print("=" * 70)
print("LAYER 5 — Multi-source Orchestrator")
print("=" * 70)

from v2_orchestrator import (V2Orchestrator, _derive_queries, _extract_jtbd_components, _detect_sources)

parts = _extract_jtbd_components(
    "When a client pays an invoice late, I want to auto-send a reminder, so I can stop chasing payments manually."
)
assert "late" in parts["trigger"].lower() and "reminder" in parts["action"].lower()
print(f"  [ok] JTBD parser: trigger='{parts['trigger'][:30]}...'")

spec = GoalSpec(raw_goal="invoice chasing", target_user="Head of Ops at a 20-person agency",
                jtbd="When a client pays an invoice late, I want to auto-send a reminder, so I can stop chasing payments manually.",
                success_signal="20 paying users", is_complete=True)
queries = _derive_queries(spec)
assert set(queries.keys()) == {"pain", "payment", "jtbd", "competition"}
print(f"  [ok] Query derivation: {sum(len(q) for q in queries.values())} queries across 4 angles")

sources = _detect_sources()
assert "serpapi" in sources and "github" in sources
print(f"  [ok] Source detection: {sources}")

orch = V2Orchestrator(spec, candidate_id="test_e2e")
orch.research = lambda: []  # Keep this end-to-end test independent of external APIs.
orch.evaluate = lambda: combine(evaluate(orch.tracker, orch.candidate_id))  # No live verifier call.
for i, t in enumerate(["Manual invoice reminders", "Wish Stripe had trigger", "Need auto-chase"]):
    orch.tracker.add(Claim(text=f"Pain: {t}", source="github_issue", source_class="verified",
                            age_days=30, category="pain", confidence=0.85,
                            independence_id=f"gh_{i}", candidate_id=orch.candidate_id))
orch.tracker.add(Claim(text="G2 review", source="g2_review", source_class="verified",
                        age_days=14, category="payment", confidence=0.9,
                        independence_id="g2_1", candidate_id=orch.candidate_id))
for name in ["InvoiceButler", "PayPing"]:
    orch.tracker.add(Claim(text=f"PH: {name}", source="ph_launch", source_class="claimed",
                            age_days=60, category="competition", confidence=0.7,
                            independence_id=f"ph_{name}", candidate_id=orch.candidate_id))
for q in ["how to auto-send invoice reminders", "best chasing late payments"]:
    from evidence_tracker import claim_from_serpapi_paa
    orch.tracker.add(claim_from_serpapi_paa(q, candidate_id=orch.candidate_id))

result = orch.run()
agg = orch.tracker.aggregate(orch.candidate_id)
assert result.claims_collected == agg.total == 8
print(f"  [ok] end-to-end run: claims_collected={result.claims_collected}, verdict={result.combined_verdict.final_verdict}")

dossier = result.to_dict()
reloaded = json.loads(json.dumps(dossier))
assert reloaded["candidate_id"] == "test_e2e"
for r in ["goal_spec", "candidate_id", "claims_collected", "sources_run", "sources_skipped",
          "combined_verdict", "elapsed_seconds", "tracker_summary"]:
    assert r in reloaded
print(f"  [ok] Dossier has all required fields")

print()
print("=" * 70)
print("[ok] ALL v2 LAYER TESTS PASSED")
print("=" * 70)
