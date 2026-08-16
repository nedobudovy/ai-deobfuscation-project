#!/usr/bin/env python3
"""
IDA Pseudocode → AI Decompilation Pipeline
==========================================
Processes every .c file in decompiled/{variant}/ through multiple AI models
and prompt strategies. Results are saved to results/{run_id}/{variant}/{model}/{prompt}/.

Usage:
    python pipeline.py                        # run all enabled models & variants
    python pipeline.py --variants build_plain # specific variant
    python pipeline.py --models gpt-4o-mini  # specific model
    python pipeline.py --prompts zero_shot few_shot
    python pipeline.py --dry-run             # list what would run, no API calls
    python pipeline.py --stats               # print stats for last run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from models_api import make_model, ModelError
from prompts import build_messages, PROMPTS

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ─── Result storage ───────────────────────────────────────────────────────────

def safe_model_name(model_id: str) -> str:
    """Convert model ID to filesystem-safe name."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", model_id)


def result_path(
    output_dir: Path,
    run_id: str,
    variant: str,
    model_id: str,
    prompt_name: str,
    filename: str,
) -> Path:
    return (
        output_dir
        / run_id
        / variant
        / safe_model_name(model_id)
        / prompt_name
        / filename
    )


def save_result(
    path: Path,
    text: str,
    metadata: dict,
    save_metadata: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if save_metadata:
        meta_path = path.with_suffix(".meta.json")
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


# ─── Stats helpers ────────────────────────────────────────────────────────────

def load_run_stats(run_dir: Path) -> dict:
    """Scan run directory and collect aggregate stats."""
    stats: dict = {
        "run_id": run_dir.name,
        "variants": {},
        "models": {},
        "prompts": {},
        "total_files": 0,
        "total_tokens": 0,
        "total_latency_s": 0.0,
        "errors": 0,
    }
    for meta_file in run_dir.rglob("*.meta.json"):
        parts = meta_file.relative_to(run_dir).parts
        # parts: variant / model / prompt / filename.meta.json
        if len(parts) < 4:
            continue
        variant, model, prompt = parts[0], parts[1], parts[2]
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        stats["total_files"] += 1
        tokens = meta.get("total_tokens") or 0
        latency = meta.get("latency_s") or 0.0
        error = meta.get("error", False)

        stats["total_tokens"] += tokens
        stats["total_latency_s"] += latency
        if error:
            stats["errors"] += 1

        for bucket, key in [(stats["variants"], variant),
                            (stats["models"], model),
                            (stats["prompts"], prompt)]:
            if key not in bucket:
                bucket[key] = {"files": 0, "tokens": 0, "latency_s": 0.0, "errors": 0}
            bucket[key]["files"] += 1
            bucket[key]["tokens"] += tokens
            bucket[key]["latency_s"] += latency
            if error:
                bucket[key]["errors"] += 1

    return stats


def print_stats(stats: dict) -> None:
    print(f"\n{'─'*60}")
    print(f"  Run: {stats['run_id']}")
    print(f"  Total processed : {stats['total_files']}")
    print(f"  Total tokens    : {stats['total_tokens']:,}")
    print(f"  Total latency   : {stats['total_latency_s']:.1f}s")
    print(f"  Errors          : {stats['errors']}")

    for label, bucket in [("Variants", stats["variants"]),
                           ("Models",   stats["models"]),
                           ("Prompts",  stats["prompts"])]:
        print(f"\n  {label}:")
        for name, d in sorted(bucket.items()):
            avg_lat = d["latency_s"] / d["files"] if d["files"] else 0
            print(f"    {name:<45} files={d['files']:>4}  "
                  f"tokens={d['tokens']:>8,}  "
                  f"avg_lat={avg_lat:>5.1f}s  "
                  f"errors={d['errors']}")
    print(f"{'─'*60}\n")


# ─── Core pipeline ────────────────────────────────────────────────────────────

def run_pipeline(
    cfg: dict,
    run_id: str,
    filter_variants: list[str] | None = None,
    filter_models: list[str] | None = None,
    filter_prompts: list[str] | None = None,
    max_files_override: int = 0,
    dry_run: bool = False,
) -> None:
    pipe_cfg = cfg["pipeline"]
    output_dir = Path(pipe_cfg["output_dir"])
    decompiled_root = Path("decompiled")
    request_delay = pipe_cfg.get("request_delay", 1.0)
    max_retries = pipe_cfg.get("max_retries", 3)
    timeout = pipe_cfg.get("timeout", 120)
    save_metadata = pipe_cfg.get("save_metadata", True)
    skip_existing = pipe_cfg.get("skip_existing", True)
    max_files = max_files_override or pipe_cfg.get("max_files_per_variant", 0)

    enabled_models = [m for m in cfg["models"] if m.get("enabled", False)]
    all_prompts = list(PROMPTS.keys())

    # Apply filters
    if filter_variants:
        variants = [v for v in filter_variants if (decompiled_root / v).is_dir()]
    elif pipe_cfg.get("build_variants"):
        variants = pipe_cfg["build_variants"]
    else:
        variants = sorted(p.name for p in decompiled_root.iterdir() if p.is_dir())

    if filter_models:
        enabled_models = [m for m in enabled_models if m["id"] in filter_models]

    prompts_to_run = filter_prompts if filter_prompts else all_prompts

    # Collect all (variant, file) pairs; skip macOS metadata files (._*)
    work_items: list[tuple[str, Path]] = []
    for variant in variants:
        variant_dir = decompiled_root / variant
        files = sorted(f for f in variant_dir.glob("*.c") if not f.name.startswith("._"))
        if max_files:
            files = files[:max_files]
        for f in files:
            work_items.append((variant, f))

    total_calls = len(work_items) * len(enabled_models) * len(prompts_to_run)
    log.info("Run ID       : %s", run_id)
    log.info("Variants     : %s", variants)
    log.info("Models       : %s", [m["id"] for m in enabled_models])
    log.info("Prompts      : %s", prompts_to_run)
    log.info("Input files  : %d", len(work_items))
    log.info("Total calls  : %d", total_calls)

    if dry_run:
        log.info("[dry-run] Stopping before any API calls.")
        return

    if not enabled_models:
        log.error("No enabled models found. Check config.yaml.")
        sys.exit(1)

    # Instantiate models once
    model_instances = {}
    for m_cfg in enabled_models:
        try:
            model_instances[m_cfg["id"]] = make_model(m_cfg, cfg["api_keys"], timeout)
        except Exception as exc:
            log.warning("Could not instantiate %s: %s", m_cfg["id"], exc)

    # Write run manifest
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "variants": variants,
        "models": [m["id"] for m in enabled_models],
        "prompts": prompts_to_run,
        "total_input_files": len(work_items),
        "total_api_calls": total_calls,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    completed = 0
    errors = 0

    # Track models whose daily quota is exhausted — skip them for the rest of run
    quota_exhausted: set[str] = set()

    # Network-error patterns — anything that means "the request never reached the server"
    _NETWORK_ERROR_PATTERNS = (
        "nodename nor servname",
        "Name or service not known",
        "Temporary failure in name resolution",
        "[Errno 8]",
        "Connection refused",
        "Connection reset",
        "Network is unreachable",
        "Could not resolve host",
        "ConnectError",
        "getaddrinfo failed",
    )

    def _wait_for_network() -> None:
        """
        Block until DNS + outbound connectivity returns. Polls a known endpoint
        with exponential backoff capped at 5 minutes. Never gives up.
        """
        import socket
        wait = 15
        elapsed = 0
        while True:
            try:
                socket.create_connection(("openrouter.ai", 443), timeout=5).close()
                if elapsed > 0:
                    log.info("  ✓ network restored after %ds — resuming", elapsed)
                return
            except OSError:
                log.info("  network still down; sleeping %ds (waited %ds total)…",
                         wait, elapsed)
                time.sleep(wait)
                elapsed += wait
                wait = min(int(wait * 1.5), 300)

    def _is_daily_quota(exc: Exception, retry_after: int | None) -> bool:
        """
        Detect terminal-for-this-run conditions (vs transient rate limit):
          - Retry-After > 5 min  →  daily quota
          - "credit balance too low" / "insufficient_quota" / "no credits"
            / "permission to execute"  →  account not funded
          - "daily" / "per day" / "monthly" / "billing"  →  hard quota
        """
        if retry_after and retry_after > 300:
            return True
        msg = str(exc).lower()
        return any(s in msg for s in (
            "daily", "per day", "rpd", "tpd",
            "quota exceeded", "quota_exceeded", "insufficient_quota",
            "monthly", "billing", "credit balance", "credit",
            "permission to execute",
            "doesn't have any credits", "no credits",
            "402 payment required", "payment required",
        ))

    for variant, src_file in work_items:
        pseudocode = src_file.read_text(encoding="utf-8", errors="replace")

        for model_id, model in model_instances.items():
            if model_id in quota_exhausted:
                # Bump counters and skip every remaining prompt for this model
                completed += len(prompts_to_run)
                continue

            for prompt_name in prompts_to_run:
                if model_id in quota_exhausted:
                    completed += 1
                    continue

                out_path = result_path(
                    output_dir, run_id, variant, model_id, prompt_name,
                    src_file.name,
                )

                if skip_existing and out_path.exists():
                    log.debug("skip existing: %s", out_path)
                    completed += 1
                    continue

                messages = build_messages(prompt_name, pseudocode)
                label = f"{variant}/{src_file.name} | {model_id} | {prompt_name}"

                # Use a while-loop with a separate attempt counter so network outages
                # don't consume retry attempts.
                attempt = 0
                while attempt < max_retries:
                    try:
                        log.info("[%d/%d] %s", completed + 1, total_calls, label)
                        text, meta = model.complete(messages)
                        meta["source_file"] = src_file.name
                        meta["variant"] = variant
                        meta["prompt"] = prompt_name
                        meta["run_id"] = run_id
                        meta["error"] = False
                        save_result(out_path, text, meta, save_metadata)
                        completed += 1
                        break
                    except Exception as exc:
                        exc_str = str(exc)

                        # Network error: pause the pipeline until connectivity returns.
                        # Does NOT consume a retry attempt — the call never reached the server.
                        if any(p in exc_str for p in _NETWORK_ERROR_PATTERNS):
                            log.warning("  ⏸  network appears down: %s", exc_str[:120])
                            _wait_for_network()
                            continue  # retry without incrementing attempt

                        attempt += 1
                        log.warning("  attempt %d/%d failed: %s", attempt, max_retries, exc)

                        # 413 Payload Too Large — permanent error, retrying never helps
                        if "413" in exc_str and "Payload Too Large" in exc_str:
                            log.info("  413 is permanent; skipping cell (no retries)")
                            errors += 1
                            err_meta = {
                                "error": True, "error_message": exc_str,
                                "error_type": "payload_too_large",
                                "model_id": model_id, "variant": variant,
                                "prompt": prompt_name, "source_file": src_file.name,
                            }
                            save_result(out_path.with_suffix(".error.txt"),
                                        exc_str, err_meta, save_metadata)
                            completed += 1
                            break

                        # Check for Retry-After header (Groq daily quota etc.)
                        retry_after_raw = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
                        retry_after = int(float(retry_after_raw)) if retry_after_raw else None

                        # Detect daily quota exhaustion — mark model dead for the run
                        MAX_RETRY_WAIT = 300
                        if _is_daily_quota(exc, retry_after):
                            log.warning(
                                "  DAILY QUOTA EXHAUSTED for %s — skipping all remaining calls "
                                "for this model (Retry-After=%s)",
                                model_id, retry_after,
                            )
                            quota_exhausted.add(model_id)
                            errors += 1
                            err_meta = {
                                "error": True,
                                "error_message": f"Daily quota exhausted: {exc}",
                                "retry_after_s": retry_after,
                                "model_id": model_id,
                                "variant": variant,
                                "prompt": prompt_name,
                                "source_file": src_file.name,
                            }
                            save_result(out_path.with_suffix(".error.txt"),
                                        str(exc), err_meta, save_metadata)
                            completed += 1
                            break  # exit retry loop; outer model-loop will skip on next file

                        elif attempt == max_retries:
                            errors += 1
                            err_meta = {
                                "error": True,
                                "error_message": str(exc),
                                "model_id": model_id,
                                "variant": variant,
                                "prompt": prompt_name,
                                "source_file": src_file.name,
                            }
                            save_result(out_path.with_suffix(".error.txt"),
                                        str(exc), err_meta, save_metadata)
                            completed += 1
                        else:
                            # Longer backoff for rate limits (429): 15s, 30s, 60s…
                            is_rate_limit = "429" in str(exc)
                            wait = (15 * attempt) if is_rate_limit else (2 ** attempt)
                            if retry_after:
                                wait = min(retry_after + 2, MAX_RETRY_WAIT)
                            log.info("  waiting %ds before retry…", wait)
                            time.sleep(wait)

                time.sleep(request_delay)

    # Write final stats
    final_stats = load_run_stats(run_dir)
    final_stats["finished_at"] = datetime.now(timezone.utc).isoformat()
    (run_dir / "stats.json").write_text(json.dumps(final_stats, indent=2))

    log.info("Done. Completed=%d, Errors=%d", completed - errors, errors)
    print_stats(final_stats)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="IDA pseudocode → AI pipeline")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--variants", nargs="+", help="Limit to these build variants")
    parser.add_argument("--models", nargs="+", help="Limit to these model IDs")
    parser.add_argument("--prompts", nargs="+", choices=list(PROMPTS.keys()),
                        help="Limit to these prompt strategies")
    parser.add_argument("--run-id", help="Custom run ID (default: timestamp)")
    parser.add_argument("--max-files", type=int, default=0, metavar="N",
                        help="Limit to first N files per variant (0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would run without making API calls")
    parser.add_argument("--stats", action="store_true",
                        help="Print stats for the latest run and exit")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.stats:
        output_dir = Path(cfg["pipeline"]["output_dir"])
        runs = sorted(output_dir.iterdir()) if output_dir.exists() else []
        if not runs:
            print("No runs found.")
            return
        latest = runs[-1]
        stats = load_run_stats(latest)
        print_stats(stats)
        return

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_pipeline(
        cfg=cfg,
        run_id=run_id,
        filter_variants=args.variants,
        filter_models=args.models,
        filter_prompts=args.prompts,
        max_files_override=args.max_files,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
