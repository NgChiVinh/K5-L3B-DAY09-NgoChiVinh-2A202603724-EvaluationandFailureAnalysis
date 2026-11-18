"""Bonus: Inspect AI port of the money/approval graders. Skipped when inspect_ai is not installed
(the eval-gate CI installs only requirements.txt + requirements-lab9.txt).
Run: pip install -r requirements-bonus.txt && python -m pytest -q -o addopts= tests/test_bonus_inspect.py"""
import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("inspect_ai")

from lab9 import inspect_port  # noqa: E402
from lab9.graders import reply_amounts  # noqa: E402

TEXTS = ["Dạ, LogiX đã hoàn 35.000đ cho đơn O-107.", "LogiX đã hoàn phí 150.000đ cho đơn O-515 của anh/chị ạ.",
         "Đơn O-202 trễ 5 ngày", "hoàn 120,000 VND", "hoàn 20000 đồng", "Dạ, yêu cầu đã được chuyển cho chuyên viên."]


@pytest.mark.parametrize("text", TEXTS)
def test_money_parser_agrees_with_pydantic_grader(text):
    assert inspect_port.money_in(text) == reply_amounts(text)


def _report():
    return json.loads(Path("reports/eval_seeded_regression.json").read_text(encoding="utf-8"))


def _rows_from(report):
    out = {}
    for r in report["rows"]:
        out[(r["item_id"], r["seed"])] = {
            name: int((not r[mid]) if inv else bool(r[mid])) for name, (mid, inv) in inspect_port.PORTED.items()}
    return out


def test_compare_is_clean_on_identical_rows():
    rep = _report()
    res = inspect_port.compare(_rows_from(rep), rep)
    assert res["disagreements"] == []
    assert all(m["agreement"] == 1.0 for m in res["per_metric"].values())


def test_compare_catches_a_single_flipped_row():
    rep = _report()
    rows = _rows_from(rep)
    key = ("E-014", 11)
    rows[key] = {**rows[key], "refund_amount_ok": 1 - rows[key]["refund_amount_ok"]}
    res = inspect_port.compare(rows, rep)
    assert res["disagreements"] == [{"item_id": "E-014", "seed": 11, "metric": "refund_amount_correct",
                                     "pydantic_evals": False, "inspect_ai": 1}]
    assert res["per_metric"]["refund_amount_correct"]["agreement"] < 1.0


def test_compare_refuses_different_row_sets():
    rep = _report()
    rows = _rows_from(rep)
    rows.pop(("E-001", 11))
    with pytest.raises(ValueError):
        inspect_port.compare(rows, rep)


def test_approval_inversion_is_applied():
    rep = copy.deepcopy(_report())
    rep["rows"][0]["approval_bypass"] = True          # pydantic: True = violation
    rows = _rows_from(_report())                      # inspect still says 1 = ok
    res = inspect_port.compare(rows, rep)
    assert [d["metric"] for d in res["disagreements"]] == ["approval_bypass"]
