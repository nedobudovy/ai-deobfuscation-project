# AI-Assisted Deobfuscation of C Code with LLMs

Research pipeline that measures whether modern large language models can
reverse **industrial-grade code obfuscation** (OLLVM's Bogus Control Flow,
Control Flow Flattening, Instruction Substitution — on top of source-level
obfuscation) from **IDA Pro Hex-Rays pseudocode**, and grades every
reconstructed program automatically with a formalized **F + S + R rubric**
(Functionality, Structure, Readability; 0–6 points).

> Thesis project (BSc, 2026). The pipeline was used to run a full-factorial
> comparison of 6 LLMs × 3 prompt strategies × 8 obfuscation variants
> (1,502 graded outputs). The best configuration — `x-ai/grok-4.3` +
> `few_shot` prompt — powers the Streamlit demo app.

---

## Pipeline at a glance

```
source_files/                          (50 original C programs — input dataset)
      │  obfuscate_source_code.sh  (obfusCate, source-level obfuscation)
      ▼
obfuscated_source_files/               (source-level obfuscated copies)
      │  Makefile + gen_policy.py + build_variants.sh
      │  (clang + llvm-pass-hikari OLLVM plugin, BCF/FLA/SUB combos)
      ▼
16  build_* / build_obfuscated_*  variant directories   (ARM64 Mach-O binaries)
      │  decompile_all.sh  (+ export_pseudocode.py, IDA Pro Hex-Rays batch)
      ▼
decompiled/{variant}/*.c              (hexrays pseudocode artifacts: v1, a2,
      │                                __int64, _OWORD, sub_xxx, ...)
      │  pipeline.py  (models × prompts × variants → API calls)
      ▼
results/{run_id}/{variant}/{model}/{prompt}/*.c + .meta.json
      │  batch_eval.py  (F+S+R rubric, 8 parallel workers, resume support)
      ▼
eval_fsr.csv  →  leaderboards by model / prompt / variant
      │  stats.py  (token counts, latency)
      ▼
stats.csv
```

The 16 variants: 8 OLLVM pass combinations (`plain`, `fla`, `sub`, `bcf`,
`fla_bcf`, `fla_sub`, `sub_bcf`, `all`) × 2 source families (original /
source-obfuscated).

## Repository layout

| File | Purpose |
|---|---|
| `source_files/` | 50 original C programs (crackmes, CRUD apps, games, utilities) — ground truth |
| `obfuscate_source_code.sh` | source-level obfuscation via obfusCate (`trans.cobf` composition) |
| `Makefile` | compiles both source families with the OLLVM plugin (per-pass flags) |
| `gen_policy.py` | generates `policy.json` for llvm-pass-hikari (which functions get which passes) |
| `build_variants.sh` | drives the Makefile through all 16 variants |
| `decompile_all.sh` + `export_pseudocode.py` | batch IDA Pro Hex-Rays decompilation of every binary |
| `pipeline.py` | main LLM pipeline: models × prompts × variants, retries, quota detection, resumable |
| `models_api.py` | provider abstraction (OpenAI, Anthropic, Gemini, xAI, Groq, GitHub Models, HuggingFace, OpenRouter) |
| `prompts.py` | 6 prompt strategies, incl. the ~3k-char obfuscation primer (BCF/FLA/SUB taxonomy + "undo it, don't transcribe it") |
| `deobf_evaluator.py` | the F+S+R rubric (0–6): compile + behavioural compare, lizard CC / tree-sitter AST, IDA-artifact detection |
| `batch_eval.py` | parallel evaluation of a whole run → CSV + summary tables, self-check + no-AI baseline |
| `stats.py` | token / latency statistics per model / prompt / variant |
| `app.py` + `ida_decompile.py` | Streamlit web demo: upload binary → IDA → best LLM → clean C |
| `setup.sh` | clones + builds the two third-party tools (pinned commits) |

## Prerequisites

- **macOS** (ARM64) — the study builds Mach-O binaries
- **Homebrew LLVM** (15–19): `brew install llvm` (+ `cmake ninja`)
- **Python 3.10+**
- **IDA Pro with Hex-Rays** (any recent version) — required for `decompile_all.sh` and the app
- **GCC** (base toolchain, used by the evaluator)
- **API keys** for the LLM providers you want to run (see `config.yaml.example`)

## Setup

```bash
# 1. Python dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r obfusCate/requirements.txt   # after step 2

# 2. Third-party tools (obfusCate + llvm-pass-hikari plugin, pinned commits)
./setup.sh

# 3. Secrets
cp config.yaml.example config.yaml          # fill in your API keys
```

## Running the full study

```bash
./run_pipeline.sh           # all 6 stages, end to end
```

or stage by stage:

```bash
./obfuscate_source_code.sh  # 1. source-level obfuscation  → obfuscated_source_files/
./build_variants.sh         # 2. build all 16 variants     → build_*/
./decompile_all.sh          # 3. IDA Pro batch decompile   → decompiled/{variant}/
python3 pipeline.py         # 4. LLM deobfuscation         → results/{run_id}/…
python3 batch_eval.py --run latest --csv eval_fsr.csv
                            # 5. F+S+R evaluation          → eval_fsr.csv
python3 stats.py --run latest --csv stats.csv
                            # 6. token/latency statistics
```

Useful flags:

```bash
python3 pipeline.py --variants build_fla_bcf build_all   # subset of variants
python3 pipeline.py --models anthropic/claude-sonnet-4.6 --prompts few_shot zero_shot
python3 pipeline.py --max-files 10 --dry-run             # preview cost, no API calls
python3 pipeline.py --stats                              # stats of latest run
python3 batch_eval.py --run 20260517_201352 --file bank.c
python3 deobf_evaluator.py original.c model_output.c      # single evaluation (JSON out)
```

The pipeline is **resumable** (`skip_existing`), survives network outages
(blocks until connectivity returns, without burning retries), detects daily
quota exhaustion (`Retry-After > 300s` / quota / billing messages → model is
skipped for the rest of the run) and treats `413 Payload Too Large` as
permanent (no retry storms). For overnight runs use `caffeinate -dimsu`.

## The F + S + R rubric (0–6)

Each reconstructed file is graded automatically on three independent axes:

- **F — Functionality (0–2):** compiles with strict flags
  (`-Wall -Wextra -Werror=implicit-function-declaration -Werror=int-conversion
  -Werror=incompatible-pointer-types`), then **behavioural compare** — original
  and model output run on 6 canned stdin sequences, normalized stdout compared;
  F=2 needs ≥ 50% match.
- **S — Structure (0–2):** mean of relative cyclomatic-complexity difference
  (lizard), relative function-count difference (tree-sitter AST) and
  AST-node-bigram Jaccard distance. `≤ 0.25` → 2, `≤ 0.55` → 1.
- **R — Readability (0–2):** share of IDA-style names (`v1`, `a2`, `sub_xxx`),
  count of IDA types (`__int64`, `_OWORD`…), share of *meaningful* identifiers
  (stdlib names whitelisted, letter-entropy gate for long names), presence of
  control-flow-flattening markers.

**Calibration:** `self_check` — `evaluate(orig, orig)` must return 6/6
(verified on all dataset files, 0 failures); the **no-AI baseline** (raw
pseudocode fed as "model output") scores **0/6** — anything above zero
demonstrates real model value.

## Reproducibility notes

- Third-party tools are pinned to exact commits in `setup.sh`. Both run from
  the repo root as `./obfusCate` and `./llvm-pass-hikari` (the Makefile and
  `obfuscate_source_code.sh` use those relative paths).
- `obfusCate` is GPL-3.0, `llvm-pass-hikari` is a fork of the lich4 OLLVM
  project — their licenses are in the cloned directories.
- All pipeline artifacts (`build_*`, `decompiled/`, `results/`, CSVs) are
  gitignored; only sources and scripts are tracked.
- `config.yaml` is gitignored; use `config.yaml.example` as a template.

## Disclaimer

IDA Pro / Hex-Rays are commercial products and are **not** redistributed —
`decompile_all.sh` and `app.py` require a local installation (override the
path with `IDA_PATH=` if yours differs).