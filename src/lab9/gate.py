"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    graded = _graded_metrics(spec)
    for who, rep in (("PR", pr), ("baseline", base)):
        problems += [f"{who}: {p}" for p in _report_problems(rep, graded, spec)]
    if not problems:
        # Same (item, seed) universe as the baseline: no cherry-picked seeds, no dropped items.
        pr_keys, base_keys = _row_keys(pr), _row_keys(base)
        if pr_keys != base_keys:
            only_pr, only_base = sorted(pr_keys - base_keys), sorted(base_keys - pr_keys)
            problems.append(f"(item, seed) set differs from baseline: {len(only_base)} missing "
                            f"{only_base[:3]}, {len(only_pr)} extra {only_pr[:3]}")
        if sorted(pr["manifest"].get("seeds") or []) != sorted(base["manifest"].get("seeds") or []):
            problems.append(f"seeds {pr['manifest'].get('seeds')} != baseline {base['manifest'].get('seeds')}")
        # Slice tags are a property of the item (derived from its label), never of the run.
        pr_sl, base_sl = _slices_by_item(pr), _slices_by_item(base)
        moved = sorted(i for i in pr_sl.keys() & base_sl.keys() if pr_sl[i] != base_sl[i])
        if moved:
            problems.append(f"slice tags differ from baseline for {moved[:5]}")
        disk = _slices_on_disk(spec, pr)
        if disk is not None:
            wrong = sorted(i for i, tags in pr_sl.items() if i in disk and tags != disk[i])
            if wrong:
                problems.append(f"slice tags differ from {spec['eval_set']} for {wrong[:5]}")
    if base_spec is not None:
        problems += _ratchet(spec, base_spec)
    return problems


_LOWER_IS_STRICTER = ("max", "max_increase", "max_regressed_items")
_HIGHER_IS_STRICTER = ("min",)


def _graded_metrics(spec: dict) -> list[str]:
    from lab9.graders import GRADERS
    return [m["id"] for m in spec.get("metrics") or [] if m["id"] in GRADERS]


def _row_keys(rep: dict) -> set[tuple[str, int]]:
    return {(r.get("item_id"), r.get("seed")) for r in rep.get("rows") or []}


def _slices_by_item(rep: dict) -> dict[str, tuple[str, ...]]:
    out: dict[str, set[tuple[str, ...]]] = {}
    for r in rep.get("rows") or []:
        out.setdefault(r.get("item_id"), set()).add(tuple(sorted(r.get("slices") or [])))
    # An item whose rows disagree with each other is itself a relabel; make it differ from anything.
    return {i: (next(iter(v)) if len(v) == 1 else ("<inconsistent>",)) for i, v in out.items()}


def _slices_on_disk(spec: dict, rep: dict) -> dict[str, tuple[str, ...]] | None:
    """Slice tags from the committed eval set, when it is the very file the report was run on."""
    from lab9 import evalset
    path = Path(spec.get("eval_set") or "")
    if not spec.get("eval_set") or not path.is_file():
        return None
    if evalset.sha12(path) != rep.get("manifest", {}).get("eval_set_sha"):
        return None
    return {it["item_id"]: tuple(sorted(it.get("slices") or [])) for it in evalset.load_jsonl(path)}


def _finite(v) -> bool:
    return not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _report_problems(rep: dict, graded: list[str], spec: dict) -> list[str]:
    problems: list[str] = []
    rows, manifest = rep.get("rows") or [], rep.get("manifest") or {}
    bad = sorted(k for k, v in (rep.get("metrics") or {}).items() if not _finite(v))
    if bad:
        problems.append(f"metric(s) {bad} are NaN/inf")
    if rep.get("evaluator_failures"):
        problems.append(f"{len(rep['evaluator_failures'])} evaluator failure(s), first: {rep['evaluator_failures'][0]}")
    want = int(manifest.get("items") or 0) * len(manifest.get("seeds") or [])
    if len(rows) != want:
        problems.append(f"{len(rows)} rows, manifest says {manifest.get('items')} items x "
                        f"{len(manifest.get('seeds') or [])} seeds = {want}")
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        problems.append(f"duplicate (item, seed) rows {dup[:3]}")
    stray = sorted({s for _, s in keys} - set(manifest.get("seeds") or []))
    if stray:
        problems.append(f"rows for seeds {stray} not in manifest")
    if len({i for i, _ in keys}) != int(manifest.get("items") or 0):
        problems.append(f"{len({i for i, _ in keys})} distinct items, manifest says {manifest.get('items')}")
    missing = [(r.get("item_id"), r.get("seed"), m) for r in rows for m in graded
               if m in (rep.get("metrics") or {}) and r.get(m) is None]
    if missing:
        problems.append(f"{len(missing)} graded value(s) are None (crashed grader?), first {missing[0]}")
    nonbool = [(r.get("item_id"), r.get("seed"), m) for r in rows for m in graded
               if r.get(m) is not None and not isinstance(r.get(m), bool)]
    if nonbool:
        problems.append(f"{len(nonbool)} graded value(s) are not booleans, first {nonbool[0]}")
    if not problems:
        problems += _recompute_problems(rep, graded, spec)
    return problems


def _recompute_problems(rep: dict, graded: list[str], spec: dict) -> list[str]:
    """Headline metrics must be what the rows say. A hand-edited number is not evidence."""
    from lab9.eval import aggregate
    stated = rep.get("metrics") or {}
    ids = [m for m in graded if m in stated]
    rows = [{**r, "cost": r.get("cost", 0.0)} for r in rep.get("rows") or []]
    try:
        again = aggregate(rows, ids, k_values=spec.get("pass_k", []))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return ["metrics cannot be recomputed from rows"]
    if any("cost" not in r for r in rep.get("rows") or []):
        again.pop("cost_per_success", None)
    if not all("pass_rate" in r for r in rep.get("rows") or []):
        for k in [k for k in again if k == "pass_5" or k.startswith(("pass_at_", "pass_hat_"))]:
            again.pop(k)
    off = sorted(k for k, v in again.items() if k in stated and not _close(stated[k], v))
    return [f"metric(s) {off} do not match their rows (stated {[stated[k] for k in off]}, "
            f"rows give {[again[k] for k in off]})"] if off else []


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= 1e-3


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval-set version: thresholds may only get stricter. A new version starts fresh."""
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []
    problems: list[str] = []
    cur = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        mid = m["id"]
        if mid not in cur:
            problems.append(f"RATCHET metric {mid} removed from spec")
            continue
        problems += [f"RATCHET {mid}: {p}" for p in _looser(m, cur[mid], m.get("threshold") or {},
                                                             cur[mid].get("threshold") or {})]
    cur_sl = {(s["name"], s["metric"]): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        key = (s["name"], s["metric"])
        if key not in cur_sl:
            problems.append(f"RATCHET slice {key[0]}/{key[1]} removed from spec")
            continue
        problems += [f"RATCHET slice {key[0]}/{key[1]}: {p}" for p in _looser(s, cur_sl[key], s, cur_sl[key])]
    bb, cb = base_spec.get("bootstrap") or {}, spec.get("bootstrap") or {}
    if cb.get("confidence", 0.95) < bb.get("confidence", 0.95):
        problems.append(f"RATCHET bootstrap confidence {cb.get('confidence')} < {bb.get('confidence')}")
    if sorted(spec.get("seeds") or []) != sorted(base_spec.get("seeds") or []):
        problems.append(f"RATCHET seeds changed {base_spec.get('seeds')} -> {spec.get('seeds')}")
    if spec.get("eval_set") != base_spec.get("eval_set"):
        problems.append(f"RATCHET eval_set changed {base_spec.get('eval_set')} -> {spec.get('eval_set')}")
    return problems


def _looser(old_entry: dict, new_entry: dict, old_th: dict, new_th: dict) -> list[str]:
    out = []
    if old_entry.get("blocking") and not new_entry.get("blocking"):
        out.append("blocking -> non-blocking")
    for k in _LOWER_IS_STRICTER:
        if k in old_th and (k not in new_th or new_th[k] > old_th[k]):
            out.append(f"{k} {old_th[k]} -> {new_th.get(k, 'removed')}")
    for k in _HIGHER_IS_STRICTER:
        if k in old_th and (k not in new_th or new_th[k] < old_th[k]):
            out.append(f"{k} {old_th[k]} -> {new_th.get(k, 'removed')}")
    if old_th.get("no_significant_drop") and not new_th.get("no_significant_drop"):
        out.append("no_significant_drop dropped")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict = "missing"            # an empty slice cannot vouch for anything
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            if "max" in sl and val > sl["max"]:
                verdict = "fail"
            keys = sorted(set(a) & set(b))
            # Item-level ratchet: an item may not score lower than on main. Catches a bug that hits
            # 1-2 items on one seed while the baseline's own (different) failures keep every mean
            # level. Only valid when the A/A run shows 0 regressed items (deterministic MockLLM).
            if "max_regressed_items" in sl:
                worse = [k for k in keys if a[k] < b[k] - 1e-9]
                note = f"regressed {len(worse)} {worse[:3]}" if worse else note
                if len(worse) > sl["max_regressed_items"]:
                    verdict = "fail"
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                                   confidence=bs.get("confidence", 0.95))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]" + (f" · {note}" if note else "")
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop", "max_regressed_items") if k in sl}
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        lines.append(f"  {sid:<34} {val!s:<8} n_items={len(a):<3} {json.dumps(th):<36} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
