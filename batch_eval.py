#!/usr/bin/env python3
"""
batch_eval.py — запускає deobf_evaluator на всіх результатах pipeline
і виводить порівняльну таблицю F+S+R по моделях та промптах.

Usage:
    python batch_eval.py                        # останній run
    python batch_eval.py --run 20260517_201352  # конкретний run
    python batch_eval.py --file bank.c          # лише один файл
    python batch_eval.py --csv eval.csv         # + зберегти CSV
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from deobf_evaluator import evaluate, EvalResult, self_check, baseline_score
from dataclasses import asdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import os


def _eval_one(args):
    """Worker: evaluate a single (variant, model, prompt, result_path) cell."""
    variant, model, prompt, result_path_s, orig_s, run_name = args
    try:
        res = evaluate(orig_s, result_path_s)
        return {
            "run": run_name,
            "variant": variant,
            "model": model,
            "prompt": prompt,
            "file": Path(result_path_s).name,
            "F": res.F, "S": res.S, "R": res.R,
            "total": res.total, "grade": res.grade,
            "compiled": res.details["F"].get("compiled", False),
            "smoke": res.details["F"].get("smoke", ""),
            "ida_var_penalty": res.details["R"]["ida_var_penalty"],
            "ida_type_hits": res.details["R"]["ida_type_hits"],
            "meaningful_ratio": res.details["R"]["meaningful_ratio"],
            "cff_hits": res.details["R"]["cff_hits"],
            "cc_orig": res.details["S"]["cc_orig"],
            "cc_model": res.details["S"]["cc_model"],
            "fn_orig": res.details["S"]["fn_orig"],
            "fn_model": res.details["S"]["fn_model"],
            "ast_bigram_sim": res.details["S"]["ast_bigram_sim"],
            "s_combined": res.details["S"]["combined"],
        }, None
    except Exception as e:
        return None, f"{variant}/{model}/{prompt}/{Path(result_path_s).name}: {e}"

RESULTS_DIR  = Path("results")
SOURCE_DIR   = Path("source_files")   # оригінальний C-код (ground truth)
DECOMPILED   = Path("decompiled")


def resolve_run(name: str) -> Path:
    if name == "latest":
        runs = sorted(p for p in RESULTS_DIR.iterdir() if p.is_dir())
        if not runs:
            sys.exit("No runs found in results/")
        return runs[-1]
    p = RESULTS_DIR / name
    if not p.exists():
        sys.exit(f"Run not found: {p}")
    return p


def find_original(filename: str) -> Path | None:
    """Знаходить оригінальний C-файл у source_files/ (без obfuscated_ префіксу)."""
    # obfuscated variant: obfuscated_bank.c → bank.c
    clean_name = filename.removeprefix("obfuscated_")
    p = SOURCE_DIR / clean_name
    return p if p.exists() else None


def collect_result_files(run_dir: Path, filter_file: str | None) -> list[tuple]:
    """Повертає список (variant, model, prompt, result_path)."""
    items = []
    for result_path in sorted(run_dir.rglob("*.c")):
        # Пропускаємо macOS метадата файли
        if result_path.name.startswith("._"):
            continue
        # Пропускаємо .error файли
        if ".error" in result_path.suffixes:
            continue
        if filter_file and result_path.name != filter_file:
            continue

        # Структура: run_dir/variant/model/prompt/filename.c
        parts = result_path.relative_to(run_dir).parts
        if len(parts) != 4:
            continue
        variant, model, prompt, fname = parts
        items.append((variant, model, prompt, result_path))
    return items


def run_sanity_checks(items: list[tuple]) -> None:
    """
    Перевіряє: для кожного унікального оригіналу evaluate(orig, orig) == 6/6.
    Якщо ні — друкує попередження (метрика зламана для цього файлу).
    """
    print(f"\n{'─'*90}")
    print("  SANITY CHECK — evaluate(orig, orig) must return 6/6")
    print(f"{'─'*90}")
    seen = set()
    failed = 0
    for variant, model, prompt, result_path in items:
        fname = result_path.name
        if fname in seen:
            continue
        seen.add(fname)
        orig = find_original(fname)
        if orig is None:
            continue
        try:
            res = self_check(str(orig))
            status = "OK " if res.total == 6 else "FAIL"
            if res.total < 6:
                failed += 1
            print(f"  [{status}]  {fname:<35}  F={res.F} S={res.S} R={res.R}  total={res.total}/6")
        except Exception as e:
            failed += 1
            print(f"  [ERR]  {fname:<35}  {e}")
    if failed:
        print(f"\n  ⚠  {failed} files failed self-check — metric may be biased for these inputs")
    else:
        print(f"\n  ✓  All originals score 6/6 against themselves — metric calibrated")
    print(f"{'─'*90}\n")


def run_baselines(items: list[tuple]) -> list[dict]:
    """
    «No-AI baseline»: оцінює сирий IDA-псевдокод (decompiled/{variant}/{fname})
    проти оригіналу — щоб мати точку відліку «що було б, якщо взагалі не запускати LLM».
    """
    print(f"{'─'*90}")
    print("  BASELINE — evaluating raw IDA pseudocode (no AI) as floor reference")
    print(f"{'─'*90}")
    rows = []
    seen = set()
    for variant, model, prompt, result_path in items:
        fname = result_path.name
        key = (variant, fname)
        if key in seen:
            continue
        seen.add(key)
        orig = find_original(fname)
        pseudo = DECOMPILED / variant / fname
        if orig is None or not pseudo.exists():
            continue
        try:
            res = baseline_score(str(orig), str(pseudo))
            rows.append({
                "variant": variant, "file": fname,
                "F": res.F, "S": res.S, "R": res.R,
                "total": res.total, "grade": res.grade,
            })
            print(f"  {variant:<28} {fname:<32}  F={res.F} S={res.S} R={res.R}  total={res.total}/6")
        except Exception as e:
            print(f"  [ERR] {variant}/{fname}: {e}")
    print(f"{'─'*90}\n")
    return rows


def run_batch(run_dir: Path, filter_file: str | None, csv_out: str | None,
              skip_sanity: bool = False, skip_baseline: bool = False) -> None:
    items = collect_result_files(run_dir, filter_file)
    if not items:
        print("No result files found.")
        return

    if not skip_sanity:
        run_sanity_checks(items)

    baseline_rows = []
    if not skip_baseline:
        baseline_rows = run_baselines(items)

    rows = []
    total = len(items)

    # ─── Resume support: load partial CSV if it exists ──────────────────────
    done_keys: set = set()
    if csv_out and Path(csv_out).exists():
        try:
            with open(csv_out, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    rows.append(r)
                    done_keys.add((r["variant"], r["model"], r["prompt"], r["file"]))
            print(f"  Resume: loaded {len(rows)} prior evals from {csv_out}; will skip those.")
        except Exception as e:
            print(f"  Could not load partial CSV: {e}")
            rows = []

    # ─── Build work list (skip already-done) ────────────────────────────────
    work = []
    for variant, model, prompt, result_path in items:
        fname = result_path.name
        if (variant, model, prompt, fname) in done_keys:
            continue
        orig = find_original(fname)
        if orig is None:
            continue
        work.append((variant, model, prompt, str(result_path), str(orig), run_dir.name))

    if not work:
        print("  No new work — all cells already evaluated.")
    else:
        n_workers = max(1, min(8, (os.cpu_count() or 4)))
        print(f"  Parallel eval: {len(work)} cells across {n_workers} workers.")

        done = 0
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            futures = {ex.submit(_eval_one, w): w for w in work}
            for fut in as_completed(futures):
                done += 1
                row, err = fut.result()
                if err:
                    print(f"  [{done}/{len(work)}] ERROR: {err}")
                    continue
                rows.append(row)
                if csv_out:
                    file_exists = Path(csv_out).exists() and Path(csv_out).stat().st_size > 0
                    with open(csv_out, "a", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=list(row.keys()))
                        if not file_exists:
                            w.writeheader()
                        w.writerow(row)
                print(f"  [{done}/{len(work)}] {row['model'][:35]:<35} | "
                      f"{row['prompt']:<10} | {row['file']:<28} "
                      f"F={row['F']} S={row['S']} R={row['R']} total={row['total']}")

    if not rows:
        print("No results evaluated.")
        return

    print_summary(rows)

    if baseline_rows:
        b_avg_total = sum(r["total"] for r in baseline_rows) / len(baseline_rows)
        b_avg_F     = sum(r["F"]     for r in baseline_rows) / len(baseline_rows)
        b_avg_S     = sum(r["S"]     for r in baseline_rows) / len(baseline_rows)
        b_avg_R     = sum(r["R"]     for r in baseline_rows) / len(baseline_rows)
        print(f"  No-AI BASELINE (raw IDA pseudocode):  "
              f"F={b_avg_F:.2f}  S={b_avg_S:.2f}  R={b_avg_R:.2f}  "
              f"total={b_avg_total:.2f}/6   ({len(baseline_rows)} files)")
        print(f"  → models must beat {b_avg_total:.2f} to demonstrate real value\n")

    if csv_out:
        fields = list(rows[0].keys())
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"\n  CSV saved: {csv_out}")


def print_summary(rows: list[dict]) -> None:
    print(f"\n{'═'*90}")
    print(f"  SUMMARY   (F=functionality  S=structure  R=readability  each 0-2,  total 0-6)")
    print(f"{'═'*90}")

    # Group by model
    by_model: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r)

    def avg(vals):
        return round(sum(vals) / len(vals), 2) if vals else 0

    header = f"  {'Model':<48} {'Files':>5}  {'F':>4}  {'S':>4}  {'R':>4}  {'Total':>6}  {'Grade':>5}  {'Compile%':>8}"
    print(header)
    print(f"  {'─'*86}")

    ranked = sorted(by_model.items(), key=lambda kv: avg([r["total"] for r in kv[1]]), reverse=True)

    for model, model_rows in ranked:
        files = len(model_rows)
        f_avg = avg([r["F"] for r in model_rows])
        s_avg = avg([r["S"] for r in model_rows])
        r_avg = avg([r["R"] for r in model_rows])
        t_avg = avg([r["total"] for r in model_rows])
        g_avg = avg([r["grade"] for r in model_rows])
        compiled_pct = round(100 * sum(1 for r in model_rows if r["compiled"]) / files)
        print(f"  {model:<48} {files:>5}  {f_avg:>4}  {s_avg:>4}  {r_avg:>4}  {t_avg:>6}  {g_avg:>5}  {compiled_pct:>7}%")

    print(f"\n  {'─'*86}")

    # By prompt
    by_prompt: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_prompt[r["prompt"]].append(r)

    print(f"\n  {'Prompt':<48} {'Files':>5}  {'F':>4}  {'S':>4}  {'R':>4}  {'Total':>6}  {'Grade':>5}")
    print(f"  {'─'*86}")
    for prompt, prompt_rows in sorted(by_prompt.items(), key=lambda kv: avg([r["total"] for r in kv[1]]), reverse=True):
        files = len(prompt_rows)
        print(f"  {prompt:<48} {files:>5}  "
              f"{avg([r['F'] for r in prompt_rows]):>4}  "
              f"{avg([r['S'] for r in prompt_rows]):>4}  "
              f"{avg([r['R'] for r in prompt_rows]):>4}  "
              f"{avg([r['total'] for r in prompt_rows]):>6}  "
              f"{avg([r['grade'] for r in prompt_rows]):>5}")

    overall_total = avg([r["total"] for r in rows])
    overall_grade = avg([r["grade"] for r in rows])
    print(f"\n  Overall avg:  total={overall_total}  grade={overall_grade}  ({len(rows)} evaluations)")
    print(f"{'═'*90}\n")


def main():
    parser = argparse.ArgumentParser(description="Batch evaluator for pipeline results")
    parser.add_argument("--run", default="latest")
    parser.add_argument("--file", default=None, help="Filter to single filename, e.g. bank.c")
    parser.add_argument("--csv", metavar="FILE", help="Save detailed CSV")
    parser.add_argument("--skip-sanity", action="store_true",
                        help="Skip self-check (evaluate(orig,orig)==6/6)")
    parser.add_argument("--skip-baseline", action="store_true",
                        help="Skip no-AI baseline (raw pseudocode scoring)")
    args = parser.parse_args()

    run_dir = resolve_run(args.run)
    print(f"\nEvaluating run: {run_dir.name}")
    print(f"Ground truth:  {SOURCE_DIR}/\n")

    run_batch(run_dir, args.file, args.csv,
              skip_sanity=args.skip_sanity,
              skip_baseline=args.skip_baseline)


if __name__ == "__main__":
    main()
