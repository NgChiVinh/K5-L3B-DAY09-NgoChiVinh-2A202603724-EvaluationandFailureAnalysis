"""Regression test from docs/trace_case.md: seeded_regression C-014@11 (FM-05 pre_deducted).

First wrong step: event 5 `proposal` {"amount_vnd": 120000} -- the shape subtracted the 30.000đ
prior refund itself, so the host's per-order cap subtracted it a second time (ledger 90.000đ,
label 120.000đ). The same claim@seed on `fixed` sends the full 150.000đ quote.
"""
import pytest

pytestmark = pytest.mark.student


def _grade(shape: str):
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9 import graders
    from lab9.trace_case import investigate

    case = investigate(shape, "C-014", 11)
    ctx = EvaluatorContext(name="trace_case", inputs={}, metadata=None, expected_output=case["label"],
                           output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})
    kinds = [e["kind"] for e in case["trial"]["events"]]
    return case, kinds, {name: cls().evaluate(ctx).value for name, cls in graders.GRADERS.items()}


def test_trace_case_c014_pre_deducted_is_caught():
    case, kinds, got = _grade("seeded_regression")
    assert "proposal" in kinds and kinds.index("refund_history") < kinds.index("proposal")
    assert case["trial"]["snapshot"]["refund_vnd"] == 90000
    assert got["proposal_correct"] is False          # first wrong step: proposal 120.000 != quote 150.000
    assert got["refund_amount_correct"] is False     # consequence in the ledger
    assert got["reply_matches_ledger"] is False      # reply says 120.000đ, ledger holds 90.000đ
    assert got["pass_rate"] is False


def test_trace_case_c014_fixed_passes():
    case, kinds, got = _grade("fixed")
    assert "proposal" not in kinds
    assert case["trial"]["snapshot"]["refund_vnd"] == 120000
    assert got["proposal_correct"] and got["refund_amount_correct"] and got["reply_matches_ledger"] and got["pass_rate"]
    assert got["approval_bypass"] is False and got["secret_leak"] is False
