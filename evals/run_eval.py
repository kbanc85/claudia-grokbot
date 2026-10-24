#!/usr/bin/env python3
# Made with Grok Bot.
"""Score claudia_scan.py precision and recall against fixtures.

Exits non-zero when precision or recall falls below --min-precision / --min-recall.
Default thresholds are the ones this conservative scanner is tuned to meet.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "scan" / "claudia_scan.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ITEMS = FIXTURES / "items.jsonl"
EXPECTED = FIXTURES / "expected.json"

# Tuned for the conservative scanner: high precision, solid recall on clear promises.
DEFAULT_MIN_PRECISION = 0.85
DEFAULT_MIN_RECALL = 0.80


def load_expected() -> dict[str, Any]:
    return json.loads(EXPECTED.read_text(encoding="utf-8"))


def run_scan(owner_names: list[str], owner_emails: list[str]) -> list[dict[str, Any]]:
    cmd = [sys.executable, str(SCAN), "--input", str(ITEMS)]
    for n in owner_names:
        cmd.extend(["--owner-name", n])
    for e in owner_emails:
        cmd.extend(["--owner-email", e])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(
            f"scanner failed ({proc.returncode}):\n{proc.stderr or proc.stdout}"
        )
    return json.loads(proc.stdout)


def matches_positive(cand: dict[str, Any], exp: dict[str, Any]) -> bool:
    if cand.get("item_id") != exp["item_id"]:
        return False
    if cand.get("direction") != exp["direction"]:
        return False
    what = (cand.get("what") or "").lower()
    needles = [n.lower() for n in exp.get("what_contains", [])]
    if needles and not any(n in what for n in needles):
        # Also allow the quote to carry the needle (scanner may shorten "what").
        quote = (cand.get("quote") or "").lower()
        if not any(n in quote for n in needles):
            return False
    due_exp = exp.get("due")
    if due_exp and cand.get("due") and cand.get("due") != due_exp:
        # Due mismatch still counts as a hit for recall if direction+what match,
        # but we track it; require due match for a true positive.
        return False
    if due_exp and not cand.get("due"):
        return False
    return True


def score(
    candidates: list[dict[str, Any]], expected: dict[str, Any]
) -> dict[str, Any]:
    positives = expected["positives"]
    negatives = set(expected["negatives"])

    matched_exp: set[int] = set()
    true_positives = 0
    false_positives = 0
    fp_detail: list[dict[str, Any]] = []
    tp_detail: list[str] = []

    for cand in candidates:
        item_id = cand.get("item_id", "")
        hit = False
        for idx, exp in enumerate(positives):
            if idx in matched_exp:
                continue
            if matches_positive(cand, exp):
                matched_exp.add(idx)
                true_positives += 1
                tp_detail.append(item_id)
                hit = True
                break
        if hit:
            continue
        # Unmatched proposal
        false_positives += 1
        fp_detail.append(
            {
                "item_id": item_id,
                "what": cand.get("what"),
                "direction": cand.get("direction"),
                "due": cand.get("due"),
                "on_negative": item_id in negatives,
            }
        )

    false_negatives = [
        positives[i]["item_id"] for i in range(len(positives)) if i not in matched_exp
    ]
    pred = true_positives + false_positives
    gold = len(positives)
    precision = (true_positives / pred) if pred else 0.0
    recall = (true_positives / gold) if gold else 0.0
    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "gold": gold,
        "predicted": pred,
        "precision": precision,
        "recall": recall,
        "tp_items": tp_detail,
        "fp_detail": fp_detail,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Score Claudia scanner precision/recall. Made with Grok Bot."
    )
    p.add_argument("--min-precision", type=float, default=DEFAULT_MIN_PRECISION)
    p.add_argument("--min-recall", type=float, default=DEFAULT_MIN_RECALL)
    args = p.parse_args(argv)

    expected = load_expected()
    cands = run_scan(expected["owner_names"], expected.get("owner_emails", []))
    result = score(cands, expected)

    print("Claudia scan eval")
    print(f"  gold positives:     {result['gold']}")
    print(f"  predicted:          {result['predicted']}")
    print(f"  true positives:     {result['true_positives']}")
    print(f"  false positives:    {result['false_positives']}")
    print(f"  false negatives:    {', '.join(result['false_negatives']) or '(none)'}")
    print(f"  precision:          {result['precision']:.3f}  (min {args.min_precision:.3f})")
    print(f"  recall:             {result['recall']:.3f}  (min {args.min_recall:.3f})")
    if result["fp_detail"]:
        print("  false positive detail:")
        for fp in result["fp_detail"]:
            print(f"    - {fp}")

    ok = (
        result["precision"] + 1e-9 >= args.min_precision
        and result["recall"] + 1e-9 >= args.min_recall
    )
    if ok:
        print("PASS")
        return 0
    print("FAIL: below threshold")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
