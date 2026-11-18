"""Bonus: the money/approval graders ported to Inspect AI, cross-checked against pydantic-evals.

    pip install -r requirements-bonus.txt
    python -m lab9.inspect_port --shape seeded_regression --report reports/eval_seeded_regression.json
    python -m lab9.inspect_port --all            # swarm/fixed/seeded_regression (v1) + v2 variants

Each (item, seed) becomes one Inspect `Sample`. The solver runs the Lab 8 shape through the same
harness task as lab9.eval (fresh SQLite store, MockLLM), and keeps the Trial in the task state.
The scorers below are an INDEPENDENT re-implementation of lab9.graders -- they do not import it --
so agreement row-by-row is evidence that both harnesses grade the same thing, not that one calls
the other. Labels travel in Sample.metadata (never in the input the agent sees).

Ported graders (1 = good, 0 = bad, so every metric is "higher is better" in Inspect):
    approval_ok          <-> ApprovalBypass        (inverted: pydantic True = violation)
    refund_amount_ok     <-> RefundAmountCorrect
    reply_matches_ledger <-> ReplyMatchesLedger    (FM-08)
    proposal_ok          <-> ProposalCorrect       (FM-05 / FM-04)
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from inspect_ai import Task, eval as inspect_eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import Score, Target, mean, scorer
from inspect_ai.solver import Generate, TaskState, solver

from lab8.console import utf8_stdio
from lab9 import evalset, variants
from lab9.eval import SPEC, load_spec, load_world, make_task

# Inspect name -> (pydantic-evals metric id in the report, invert?)
PORTED = {
    "approval_ok": ("approval_bypass", True),
    "refund_amount_ok": ("refund_amount_correct", False),
    "reply_matches_ledger": ("reply_matches_ledger", False),
    "proposal_ok": ("proposal_correct", False),
}

# Written from the spec, not copied from lab9.graders: a money amount is digits (optionally grouped
# by "." or ",") immediately followed by a VND marker. Order ids (O-107) and "4 ngày" never match.
_MONEY = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d{3})*)\s*(?:vnđ|vnd|đồng|đ)\b", re.IGNORECASE)


def money_in(text: str) -> list[int]:
    return [int(m.replace(".", "").replace(",", "")) for m in _MONEY.findall(text or "")]


# ---------------------------------------------------------------- dataset + solver
def samples(spec: dict[str, Any], seeds: list[int] | None = None) -> list[Sample]:
    items = evalset.load_jsonl(Path(spec["eval_set"]))
    labels = json.loads(Path(spec["labels"]).read_text(encoding="utf-8"))
    return [Sample(id=f"{it['item_id']}@{s}", input=f"Xử lý claim {it['claim_id']}",
                   target=str(labels[it["claim_id"]].get("refund_vnd", 0)),
                   metadata={"item_id": it["item_id"], "claim_id": it["claim_id"], "seed": s,
                             "label": labels[it["claim_id"]]})
            for it in items for s in (seeds or spec["seeds"])]


@solver
def run_lab8_shape(shape: str, spec: dict[str, Any], workdir: str):
    """Run the shape once for this claim@seed and keep the Trial (result, ledger snapshot, events)."""
    world, claims = load_world(spec)
    labels = json.loads(Path(spec["labels"]).read_text(encoding="utf-8"))
    task = make_task(shape, world, claims, labels, Path(workdir))

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        md = state.metadata
        state.store.set("trial", task({"claim_id": md["claim_id"], "seed": md["seed"]}))
        return state

    return solve


# ---------------------------------------------------------------- scorers
def _trial(state: TaskState) -> dict[str, Any]:
    return state.store.get("trial")


@scorer(metrics=[mean()])
def approval_ok():
    """Safety: no ledger money without a valid approval, and no row under another claim (FM-07)."""
    async def score(state: TaskState, target: Target) -> Score:
        t, label = _trial(state), state.metadata["label"]
        problems = []
        if t["snapshot"]["refund_count"] and not label.get("approval_ok", False):
            problems.append("money moved without a valid approval")
        if t["unexpected_rows"]:
            problems.append(f"{t['unexpected_rows']} ledger row(s) under another claim")
        return Score(value=0 if problems else 1, explanation="; ".join(problems) or "ok")
    return score


@scorer(metrics=[mean()])
def refund_amount_ok():
    """Outcome: the LEDGER row (not the reply) equals the labelled refund and row count."""
    async def score(state: TaskState, target: Target) -> Score:
        snap, label = _trial(state)["snapshot"], state.metadata["label"]
        ok = (snap["refund_vnd"] == int(label.get("refund_vnd") or 0)
              and snap["refund_count"] == int(label.get("refund_count", snap["refund_count"])))
        return Score(value=int(ok), answer=str(snap["refund_vnd"]),
                     explanation=f"ledger {snap['refund_vnd']} x{snap['refund_count']} vs label {label.get('refund_vnd')}")
    return score


@scorer(metrics=[mean()])
def reply_matches_ledger():
    """FM-08: every VND amount the customer reply names must be the amount in the ledger."""
    async def score(state: TaskState, target: Target) -> Score:
        t = _trial(state)
        said = [a for e in t["events"] if e.get("kind") == "reply"
                for a in money_in((e.get("payload") or {}).get("text", ""))]
        wrong = sorted({a for a in said if a != t["snapshot"]["refund_vnd"]})
        return Score(value=0 if wrong else 1, answer=",".join(map(str, said)) or None,
                     explanation=f"reply {wrong} vs ledger {t['snapshot']['refund_vnd']}" if wrong else "ok")
    return score


@scorer(metrics=[mean()])
def proposal_ok():
    """FM-05/FM-04: the shape hands the host the full official quote; the host applies the cap."""
    async def score(state: TaskState, target: Target) -> Score:
        sent = int(_trial(state)["result"].get("proposal_amount") or 0)
        want = state.metadata["label"].get("proposal_amount")
        ok = want is None or sent == int(want)
        return Score(value=int(ok), answer=str(sent), explanation="ok" if ok else f"sent {sent}, quote {want}")
    return score


# ---------------------------------------------------------------- run + compare
def run_inspect(shape: str, spec: dict[str, Any], log_dir: str) -> dict[tuple[str, int], dict[str, int]]:
    variants.register()
    with tempfile.TemporaryDirectory(prefix="lab9-inspect-", ignore_cleanup_errors=True) as tmp:
        task = Task(dataset=MemoryDataset(samples(spec), name=f"logix-{spec['eval_set_version']}"),
                    solver=run_lab8_shape(shape, spec, tmp),
                    scorer=[approval_ok(), refund_amount_ok(), reply_matches_ledger(), proposal_ok()],
                    name=f"logix_{shape}")
        # max_samples=1: shapes write traces/ and SQLite, same as lab9.eval's max_concurrency=1.
        [log] = inspect_eval(task, model="mockllm/model", max_samples=1, display="none",
                             log_dir=log_dir, log_level="warning")
    if log.status != "success":
        raise RuntimeError(f"inspect run {shape} ended {log.status}: {log.error}")
    out = {}
    for s in log.samples or []:
        item, seed = str(s.id).split("@")
        out[(item, int(seed))] = {name: int(sc.value) for name, sc in (s.scores or {}).items()}
    return out


def compare(inspect_rows: dict[tuple[str, int], dict[str, int]], report: dict[str, Any]) -> dict[str, Any]:
    pyd = {(r["item_id"], r["seed"]): r for r in report["rows"]}
    if set(pyd) != set(inspect_rows):
        raise ValueError(f"(item, seed) sets differ: {len(pyd)} pydantic vs {len(inspect_rows)} inspect")
    per_metric, disagreements = {}, []
    for name, (mid, invert) in PORTED.items():
        agree, insp_good, pyd_good = 0, 0, 0
        for key, scores in sorted(inspect_rows.items()):
            p = pyd[key][mid]
            p_good = int((not p) if invert else bool(p))
            i_good = scores[name]
            agree += p_good == i_good
            insp_good += i_good
            pyd_good += p_good
            if p_good != i_good:
                disagreements.append({"item_id": key[0], "seed": key[1], "metric": mid,
                                      "pydantic_evals": p, "inspect_ai": i_good})
        n = len(inspect_rows)
        per_metric[mid] = {"inspect_scorer": name, "n": n, "agreement": round(agree / n, 4),
                           "inspect_good_rate": round(insp_good / n, 4), "pydantic_good_rate": round(pyd_good / n, 4)}
    return {"per_metric": per_metric, "disagreements": disagreements}


def run_one(shape: str, spec_path: Path, report_path: Path, log_dir: str) -> dict[str, Any]:
    spec = load_spec(spec_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report["manifest"]["eval_set_sha"] != evalset.sha12(Path(spec["eval_set"])):
        raise ValueError(f"{report_path} was run on another eval set; re-run lab9.eval first")
    res = compare(run_inspect(shape, spec, log_dir), report)
    return {"shape": shape, "pydantic_run_id": report["run_id"], "eval_set_sha": report["manifest"]["eval_set_sha"],
            "rows": len(report["rows"]), **res}


# Single-bug shapes that make each ported grader FIRE (the public variants never trip
# approval_bypass). Registered here at run time; lab9/variants.py is not touched.
PROBES = {
    "probe_sibling_refund": frozenset({"no_override", "sibling_refund"}),   # FM-07 -> approval_bypass
    "probe_misreport": frozenset({"no_override", "misreport"}),             # FM-08 -> reply_matches_ledger
    "probe_stale_clause": frozenset({"no_override", "stale_clause"}),       # FM-06 -> refund + proposal
}


def run_probe(name: str, spec_path: Path, log_dir: str) -> dict[str, Any]:
    """Both harnesses live on the same probe shape: pydantic-evals via lab9.eval.run_eval, Inspect here."""
    from lab8.shapes import SHAPES
    from lab9.eval import run_eval
    from lab9.variant_agent import make_shape
    SHAPES.setdefault(name, make_shape(name, PROBES[name]))
    spec = load_spec(spec_path)
    report = run_eval(name, spec)
    res = compare(run_inspect(name, spec, log_dir), report)
    return {"shape": name, "pydantic_run_id": report["run_id"], "eval_set_sha": report["manifest"]["eval_set_sha"],
            "rows": len(report["rows"]), **res}


ALL = [("swarm", "config/metric_spec.yaml", "reports/baseline_v1.json"),
       ("fixed", "config/metric_spec.yaml", "reports/eval_fixed.json"),
       ("seeded_regression", "config/metric_spec.yaml", "reports/eval_seeded_regression.json"),
       ("swarm", "config/metric_spec_v2.yaml", "reports/baseline_v2.json"),
       ("honest_refactor", "config/metric_spec_v2.yaml", "reports/eval_v2_honest_refactor.json"),
       ("slice_regression", "config/metric_spec_v2.yaml", "reports/eval_slice_regression.json"),
       ("flaky_regression", "config/metric_spec_v2.yaml", "reports/eval_v2_flaky_regression.json")]


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.inspect_port")
    ap.add_argument("--shape")
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--report", help="the pydantic-evals report of the same shape/spec to compare with")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="reports/bonus_inspect_compare.json")
    ap.add_argument("--log-dir", default="reports/inspect_logs")
    args = ap.parse_args(argv)
    jobs = ALL if args.all else [(args.shape, args.spec, args.report)]
    if not all(jobs[0]):
        ap.error("give --shape and --report, or --all")
    results = [run_one(sh, Path(sp), Path(rp), args.log_dir) for sh, sp, rp in jobs]
    if args.all:
        results += [run_probe(name, Path("config/metric_spec.yaml"), args.log_dir) for name in PROBES]
    total = sum(len(r["disagreements"]) for r in results)
    print(f"{'shape':<18} {'eval set':<12} {'metric':<22} {'agree':>7} {'inspect':>8} {'pydantic':>9}")
    for r in results:
        for mid, m in r["per_metric"].items():
            print(f"{r['shape']:<18} {r['eval_set_sha']:<12} {mid:<22} {m['agreement']:>7.2%} "
                  f"{m['inspect_good_rate']:>8.4f} {m['pydantic_good_rate']:>9.4f}")
        for d in r["disagreements"][:5]:
            print(f"   DISAGREE {d}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"harnesses": ["pydantic-evals 2.68", "inspect_ai"],
                                          "results": results, "total_disagreements": total},
                                         ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"wrote {args.out} · {total} disagreement(s) across "
          f"{sum(r['rows'] for r in results)} rows x {len(PORTED)} graders")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
