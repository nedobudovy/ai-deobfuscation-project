#!/usr/bin/env python3
"""
Statistics & analysis for pipeline results.

Usage:
    python stats.py                    # all runs summary
    python stats.py --run 20260514_120000
    python stats.py --run latest --csv stats.csv
    python stats.py --compare run1 run2
    python stats.py --leaderboard      # models ranked by avg latency
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


RESULTS_DIR = Path("results")


def iter_meta_files(run_dir: Path):
    for f in run_dir.rglob("*.meta.json"):
        if "error" not in f.name:
            try:
                yield json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass


def resolve_run(name: str) -> Path:
    if name == "latest":
        runs = sorted(RESULTS_DIR.iterdir())
        if not runs:
            sys.exit("No runs found in results/")
        return runs[-1]
    p = RESULTS_DIR / name
    if not p.exists():
        sys.exit(f"Run not found: {p}")
    return p


def collect_rows(run_dir: Path) -> list[dict]:
    rows = []
    for meta in iter_meta_files(run_dir):
        rows.append({
            "run_id": run_dir.name,
            "variant": meta.get("variant", ""),
            "file": meta.get("source_file", ""),
            "model": meta.get("model_id", ""),
            "prompt": meta.get("prompt", ""),
            "latency_s": meta.get("latency_s", 0.0),
            "prompt_tokens": meta.get("prompt_tokens", 0),
            "completion_tokens": meta.get("completion_tokens", 0),
            "total_tokens": meta.get("total_tokens", 0),
            "finish_reason": meta.get("finish_reason", ""),
            "error": meta.get("error", False),
        })
    return rows


def aggregate(rows: list[dict], group_by: str) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for r in rows:
        key = r[group_by]
        if key not in groups:
            groups[key] = {"count": 0, "tokens": 0, "latency": 0.0, "errors": 0}
        groups[key]["count"] += 1
        groups[key]["tokens"] += r["total_tokens"] or 0
        groups[key]["latency"] += r["latency_s"] or 0.0
        if r["error"]:
            groups[key]["errors"] += 1
    return groups


def print_table(title: str, groups: dict[str, dict], key_label: str = "Key") -> None:
    print(f"\n  {title}")
    print(f"  {'─'*80}")
    header = f"  {key_label:<40}  {'Files':>6}  {'Tokens':>10}  {'AvgLat':>8}  {'Errors':>6}"
    print(header)
    print(f"  {'─'*80}")
    for name in sorted(groups):
        d = groups[name]
        avg_lat = d["latency"] / d["count"] if d["count"] else 0
        print(
            f"  {name:<40}  {d['count']:>6}  {d['tokens']:>10,}  "
            f"{avg_lat:>7.2f}s  {d['errors']:>6}"
        )


def cmd_summary(run_dir: Path, csv_out: str | None) -> None:
    rows = collect_rows(run_dir)
    if not rows:
        print(f"No metadata found in {run_dir}")
        return

    print(f"\n{'═'*82}")
    print(f"  Run: {run_dir.name}   ({len(rows)} results)")
    print(f"{'═'*82}")

    print_table("By model",   aggregate(rows, "model"),   "Model")
    print_table("By prompt",  aggregate(rows, "prompt"),  "Prompt")
    print_table("By variant", aggregate(rows, "variant"), "Variant")

    total_tok = sum(r["total_tokens"] or 0 for r in rows)
    total_lat = sum(r["latency_s"] or 0 for r in rows)
    errors = sum(1 for r in rows if r["error"])
    print(f"\n  Totals: files={len(rows)}, tokens={total_tok:,}, "
          f"latency={total_lat:.1f}s, errors={errors}")
    print(f"{'═'*82}\n")

    if csv_out:
        fields = list(rows[0].keys()) if rows else []
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"  CSV saved: {csv_out}")


def cmd_compare(run_names: list[str]) -> None:
    all_runs: dict[str, list[dict]] = {}
    for name in run_names:
        rd = resolve_run(name)
        all_runs[rd.name] = collect_rows(rd)

    # Compare models across runs
    print(f"\n{'═'*82}")
    print("  Cross-run comparison (avg latency per model)")
    print(f"{'═'*82}")

    models: set[str] = set()
    for rows in all_runs.values():
        models.update(r["model"] for r in rows)

    col_w = 12
    header = f"  {'Model':<40}" + "".join(f"  {n[:col_w]:>{col_w}}" for n in all_runs)
    print(header)
    print(f"  {'─'*80}")

    for model in sorted(models):
        row_str = f"  {model:<40}"
        for run_name, rows in all_runs.items():
            model_rows = [r for r in rows if r["model"] == model]
            if model_rows:
                avg = sum(r["latency_s"] or 0 for r in model_rows) / len(model_rows)
                row_str += f"  {avg:>{col_w}.2f}s"
            else:
                row_str += f"  {'—':>{col_w}}"
        print(row_str)
    print()


def cmd_leaderboard(run_dir: Path) -> None:
    rows = [r for r in collect_rows(run_dir) if not r["error"]]
    if not rows:
        print("No successful results found.")
        return

    by_model = aggregate(rows, "model")
    ranked = sorted(
        by_model.items(),
        key=lambda kv: kv[1]["latency"] / kv[1]["count"] if kv[1]["count"] else float("inf"),
    )

    print(f"\n{'═'*60}")
    print(f"  Leaderboard (fastest avg latency) — {run_dir.name}")
    print(f"{'═'*60}")
    for i, (model, d) in enumerate(ranked, 1):
        avg_lat = d["latency"] / d["count"] if d["count"] else 0
        avg_tok = d["tokens"] / d["count"] if d["count"] else 0
        print(f"  {i:>2}. {model:<40}  {avg_lat:>7.2f}s  {avg_tok:>8,.0f} tok/req")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline statistics")
    parser.add_argument("--run", default="latest", help="Run ID or 'latest'")
    parser.add_argument("--csv", metavar="FILE", help="Export raw rows to CSV")
    parser.add_argument("--compare", nargs="+", metavar="RUN", help="Compare multiple runs")
    parser.add_argument("--leaderboard", action="store_true")
    args = parser.parse_args()

    if args.compare:
        cmd_compare(args.compare)
        return

    run_dir = resolve_run(args.run)

    if args.leaderboard:
        cmd_leaderboard(run_dir)
        return

    cmd_summary(run_dir, args.csv)


if __name__ == "__main__":
    main()
