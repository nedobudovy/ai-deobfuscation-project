# AI-Assisted Deobfuscation of C Code with Large Language Models

End-to-end research pipeline that measures whether modern LLMs can reverse
**industrial-grade code obfuscation** — OLLVM's *Bogus Control Flow (BCF)*,
*Control Flow Flattening (FLA)* and *Instruction Substitution (SUB)* applied
on top of source-level obfuscation — from **IDA Pro Hex-Rays pseudocode**,
and automatically grades every reconstructed program with a formalized
**F + S + R rubric** (Functionality, Structure, Readability; 0–6 points).

Built as a bachelor thesis (2026) and used to run a full-factorial comparison
of **6 LLMs × 3 prompt strategies × 8 obfuscation variants**, producing
1,502 automatically graded outputs.

---

## Table of contents

- [Key results](#key-results)
- [Pipeline](#pipeline)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Usage](#usage)
  - [Full run](#full-run)
  - [Stage by stage](#stage-by-stage)
  - [LLM pipeline options](#llm-pipeline-options)
  - [Evaluation options](#evaluation-options)
- [The F + S + R rubric](#the-f--s--r-rubric)
- [Prompt strategies](#prompt-strategies)
- [Web demo](#web-demo)
- [Reproducibility](#reproducibility)
- [License & third-party tools](#license--third-party-tools)

---

## Key results

| Model | N | F | S | R | **Total** | Compile % |
|---|---:|---:|---:|---:|---:|---:|
| **x-ai/grok-4.3** | 240 | 1.90 | 1.79 | 1.87 | **5.57 / 6** | 100 |
| anthropic/claude-sonnet-4.6 | 240 | 1.87 | 1.79 | 1.78 | 5.45 | 98 |
| google/gemini-2.5-pro | 239 | 1.90 | 1.57 | 1.92 | 5.38 | 100 |
| deepseek/deepseek-v3 | 240 | 1.60 | 1.72 | 1.84 | 5.16 | 84 |
| gpt-4o-mini (free) | 158 | 1.60 | 1.59 | 1.75 | 4.94 | 87 |
| llama-4-scout (free) | 123 | 1.36 | 1.83 | 1.67 | 4.85 | 72 |

- **Adding an "obfuscation primer" to the prompts** (a ~3,000-char taxonomy of
  BCF/FLA/SUB + "undo it, don't transcribe it") raised the mean score on the
  hardest variants from **4.42 → 5.39 (+0.97)**; grok-4.3 gained **+1.65**.
- **Prompt design beats obfuscation strength**: all 8 hardest variants scored
  ~5.3/6 — the expected quality-degradation gradient vanished once models were
  told the input was obfuscated.
- Best price/quality: **deepseek-v3** (~10× cheaper than gemini-2.5-pro);
  best free model: **llama-4-scout**.
- Raw IDA pseudocode ("no-AI baseline") scores **0/6** — any score above zero
  demonstrates real model value.

> The numbers above come from the `deobf_aware` run described in the thesis
> (8 hardest variants × 6 models × 3 prompts × 10 files). Re-run them with
> this repository's pipeline.

---

## Pipeline

```
source_files/                        (50 original C programs — input dataset)
      │  obfuscate_source_code.sh    (obfusCate, source-level obfuscation)
      ▼
obfuscated_source_files/             (obfuscated copies of the sources)
      │  build_variants.sh → Makefile + gen_policy.py
      │  (clang + llvm-pass-hikari OLLVM plugin, BCF / FLA / SUB combos)
      ▼
16  build_* / build_obfuscated_*  variant directories  (ARM64 Mach-O binaries)
      │  decompile_all.sh + export_pseudocode.py   (IDA Pro Hex-Rays batch)
      ▼
decompiled/{variant}/*.c             (pseudocode full of IDA artifacts:
      │                               v1, a2, __int64, _OWORD, sub_xxx …)
      │  pipeline.py                 (models × prompts × variants → API calls)
      ▼
results/{run_id}/{variant}/{model}/{prompt}/*.c   (+ .meta.json metadata)
      │  batch_eval.py               (F+S+R rubric, 8 parallel workers,
      │                               resumable, self-check + baseline)
      ▼
eval_fsr.csv  →  leaderboards by model / prompt / variant
      │  stats.py                    (token usage, latency)
      ▼
stats.csv
```

**The 16 variants** — 8 OLLVM pass combinations × 2 source families:

| Source family | `plain` | `bcf` | `fla` | `sub` | `fla_bcf` | `fla_sub` | `sub_bcf` | `all` |
|---|---|---|---|---|---|---|---|---|
| original | `build_plain` | `build_bcf` | `build_fla` | `build_sub` | `build_fla_bcf` | `build_fla_sub` | `build_sub_bcf` | `build_all` |
| obfuscated | `build_obfuscated_plain` | `build_obfuscated_bcf` | … | … | … | … | … | `build_obfuscated_all` |

---

## Repository layout

| File | Purpose |
|---|---|
| `source_files/` | 50 original C programs (crackmes, CRUD apps, games, utilities) — the ground truth |
| `trans.cobf` | obfusCate transformation composition (integer/string encoding, opaque predicates, interface randomisation, identifier renaming) |
| `obfuscate_source_code.sh` | source-level obfuscation: `source_files/` → `obfuscated_source_files/` |
| `Makefile` | compiles one pass configuration of both source families with the OLLVM plugin |
| `gen_policy.py` | generates `policy.json` for llvm-pass-hikari (module/function → pass mapping) |
| `build_variants.sh` | drives the Makefile through all 8 pass combos → 16 build directories |
| `decompile_all.sh` + `export_pseudocode.py` | batch IDA Pro Hex-Rays decompilation of every built binary |
| `pipeline.py` | the LLM pipeline: models × prompts × variants, retries, quota detection, resume support |
| `models_api.py` | provider abstraction (OpenAI, Anthropic, Gemini, xAI, Groq, GitHub Models, HuggingFace, OpenRouter) |
| `prompts.py` | 6 prompt strategies incl. the obfuscation primer + few-shot examples |
| `deobf_evaluator.py` | the F+S+R rubric: strict compile + behavioural compare, lizard CC, tree-sitter AST, readability heuristics |
| `batch_eval.py` | parallel F+S+R evaluation of a whole run → CSV + summary tables |
| `stats.py` | token / latency statistics per model / prompt / variant |
| `app.py` + `ida_decompile.py` | Streamlit web demo: upload binary → IDA → best LLM → clean C |
| `setup.sh` | clones + builds third-party tools (pinned commits, local patches applied) |
| `patches/` | the study's local patches for obfusCate and llvm-pass-hikari |
| `config.yaml.example` | model/API-key configuration template (copy to `config.yaml`) |

---

## Prerequisites

- **macOS (ARM64)** — the study produces Mach-O binaries; the Makefile drives
  the compiler through Xcode SDK paths (`xcrun --show-sdk-path`)
- **Homebrew LLVM 20–22** — the OLLVM plugin builds against the current
  `llvm` formula (header path fix for `llvm/Plugins/PassPlugin.h` is baked
  into the patch; verified on LLVM 22.1.4):
  `brew install llvm cmake ninja`
- **Python 3.10+**
- **IDA Pro with Hex-Rays** — required only for the decompilation stage and the
  web demo (set `IDA_PATH=` if your install lives elsewhere)
- **GCC** (base toolchain) — used by the evaluator to compile/run test binaries
- **LLM API keys** — see `config.yaml.example` (OpenRouter for paid models;
  Groq / GitHub Models / HuggingFace tokens for free tiers)

---

## Setup

```bash
# 1. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Third-party tools: obfusCate + llvm-pass-hikari plugin
#    (clones pinned commits, applies patches, builds Obfuscator.dylib)
./setup.sh

# 3. Secrets (config.yaml is gitignored)
cp config.yaml.example config.yaml   # fill in your API keys
```

`setup.sh` clones both repositories into `./obfusCate` and
`./llvm-pass-hikari`, installs obfusCate's Python deps and builds the
`Obfuscator.dylib` pass plugin against your Homebrew LLVM.

---

## Usage

### Full run

```bash
./run_pipeline.sh            # stages 1–6: obfuscate → build → decompile → LLM → evaluate → stats
./run_pipeline.sh 1 3        # run selected stages only
./run_pipeline.sh pipeline   # a single stage (by name)
RUN_NAME=study ./run_pipeline.sh pipeline   # pass --run-id through
```

### Stage by stage

```bash
# 1. Source-level obfuscation        → obfuscated_source_files/
./obfuscate_source_code.sh

# 2. Build all 16 variants           → build_*/ and build_obfuscated_*/
./build_variants.sh
#    …or a single configuration:    make FLA=1 BCF=1    (see `make help`)

# 3. IDA Pro batch decompilation     → decompiled/{variant}/*.c
./decompile_all.sh                   # (IDA_PATH=/path/to/idat to override)

# 4. LLM deobfuscation               → results/{run_id}/…
python3 pipeline.py

# 5. F+S+R evaluation                → eval_fsr.csv
python3 batch_eval.py --run latest --csv eval_fsr.csv

# 6. Token / latency statistics      → stats.csv
python3 stats.py --run latest --csv stats.csv
```

### LLM pipeline options

```bash
python3 pipeline.py                                  # all enabled models × prompts × variants
python3 pipeline.py --variants build_fla_bcf build_all
python3 pipeline.py --models anthropic/claude-sonnet-4.6 x-ai/grok-4.3
python3 pipeline.py --prompts simple few_shot
python3 pipeline.py --max-files 10 --dry-run         # estimate cost without API calls
python3 pipeline.py --run-id study_01                # named run (default: timestamp)
python3 pipeline.py --stats                          # summary of the latest run
```

The pipeline is designed for unattended, long-running experiments:

- **Resumable** — already-written outputs are skipped (`skip_existing`).
- **Network-fault tolerant** — on DNS/connectivity errors it blocks (with
  backoff) until the network returns, without burning retries.
- **Quota-aware** — daily-quota/billing errors (`Retry-After > 300 s`, quota,
  credit, 402 …) mark the model dead for the rest of the run instead of
  sleeping for hours; transient 429s back off exponentially.
- **413-aware** — `413 Payload Too Large` is treated as permanent and skipped
  immediately (big FLA-flattened files can exceed free-tier limits).
- Wrap overnight runs in `caffeinate -dimsu` to keep the Mac awake.

### Evaluation options

```bash
python3 batch_eval.py --run latest --csv eval_fsr.csv    # whole run, incremental CSV
python3 batch_eval.py --run 20260816_123456 --file bank.c
python3 batch_eval.py --skip-sanity --skip-baseline      # skip 6/6 self-check & baseline
python3 deobf_evaluator.py original.c model_output.c      # single file, JSON result
python3 stats.py --compare run_old run_new                # cross-run latency table
```

---

## The F + S + R rubric

Every model output is graded automatically (0–6) on three independent axes.
Implementation: `deobf_evaluator.py::evaluate(original_path, model_output_path)`.

### F — Functionality (0–2)

1. Compile with strict flags — `gcc -O0 -Wall -Wextra
   -Werror=implicit-function-declaration -Werror=int-conversion
   -Werror=incompatible-pointer-types` (implicit declarations / type mismatch
   = hard fail).
2. **Behavioural compare**: compile the *original* and the *model output*,
   run both on 6 canned stdin sequences (`""`, `"1\n"`, `"0\n"`, `"1\n0\n"`,
   `"test\n0\n"`, `"1\n1\n0\n0\n"`), compare normalized stdout.

| Score | Condition |
|---|---|
| 2 | compiles **and** behavioural match ≥ 50 % |
| 1 | compiles, match < 50 % (or crash) |
| 0 | does not compile |

### S — Structure (0–2)

Mean of three normalized distances between the model output and the original:

- relative **cyclomatic complexity** difference (lizard),
- relative **function-count** difference (tree-sitter AST),
- **AST-node-bigram Jaccard** distance.

`combined ≤ 0.25 → 2`, `combined ≤ 0.55 → 1`, else `0`.

### R — Readability (0–2)

- share of IDA-style names (`v1`, `a2`, `sub_xxx`) among *user* identifiers
  (stdlib names whitelisted out),
- count of IDA-specific types (`__int64`, `_OWORD`, `_BYTE`, …),
- share of *meaningful* identifiers (short-name whitelist, letter-entropy
  gate ≥ 1.5 rejects `aaaa`-style names),
- control-flow-flattening markers (`state == N … switch`, `switch(dispatch)`).

If the code does not compile (F = 0), R is capped at 1.

### Calibration

- **Self-check**: `evaluate(orig, orig)` must return **6/6** — enforced by
  `batch_eval.py`'s sanity pass before every run (0 failures across all
  checks so far).
- **No-AI baseline**: raw IDA pseudocode fed to the rubric scores **0/6** —
  any model score above 0 proves real value over doing nothing.

---

## Prompt strategies

`prompts.py` defines six strategies, all built around the key finding that
models must be *told* the input is obfuscated:

| Prompt | Description |
|---|---|
| `simple` | minimal instruction + obfuscation note |
| `zero_shot` | full requirements + obfuscation primer, no examples |
| `one_shot` | primer + 1 worked deobfuscation example |
| `few_shot` | primer + 3 worked examples (simple / unrolled loop / struct recovery) |
| `chain_of_thought` | explicit 4-step deobfuscation-reasoning protocol |
| `role_expert` | persona: "you wrote this code, recover it" |

The shared **obfuscation primer** teaches the model the BCF / FLA / SUB
pattern taxonomy (opaque predicates, `while(1){switch(state)}` dispatchers,
bit-twiddled arithmetic, macro junk) and instructs it to **undo** the
transformations rather than transcribe them. With the primer in place,
prompt-to-prompt differences shrink to ~0.04 points (5.37–5.41).

---

## Web demo

The study's best configuration (`x-ai/grok-4.3` + `few_shot`) is exposed as an
interactive Streamlit app — upload any binary, get clean C back:

```bash
source .venv/bin/activate
streamlit run app.py        # → http://localhost:8501
```

Flow: browser upload → headless IDA Pro Hex-Rays (`ida_decompile.py`) →
LLM query with the obfuscation primer → downloadable, compilable C file with
timing/token metrics. Model and prompt are switchable in the sidebar.

---

## Reproducibility

- `setup.sh` pins **exact commits** of both third-party tools and applies the
  study's local patches from `patches/` (verified to apply cleanly):
  - obfusCate `7b90fbe` + macOS fixes (fake-libc include path, void-param
    crash fix, hex-literal encoding, `fd_set` typedef),
  - llvm-pass-hikari `d9fab4e` + BCF iterator fix, optimizer-last pass
    registration (needed for the plugin to run under clang `-O0`) and the
    modern `llvm/Plugins/PassPlugin.h` include required by LLVM ≥ 20
    (verified building with Homebrew LLVM 22.1.4).
- `setup.sh` and `obfuscate_source_code.sh` prefer an active venv
  (`VIRTUAL_ENV` / `.venv`) — Homebrew's system Python refuses pip installs
  (PEP 668). Run setup from a venv, or let it warn and fall back.
- The Makefile hard-assigns `CC` (make's built-in `CC=cc` would otherwise win
  over `?=` and the plugin would be rejected with an API-version error);
  override with `make CC=/path/to/clang` if your LLVM lives elsewhere.
- obfusCate must run with its working directory inside `./obfusCate`
  (fake-libc include paths are CWD-relative) — `obfuscate_source_code.sh`
  handles this.
- Everything generated — `obfuscated_source_files/`, `build_*/`,
  `decompiled/`, `results/`, CSVs, `config.yaml` — is gitignored; the repo
  tracks only sources, scripts and configuration templates.

## License & third-party tools

- This repository's pipeline code and dataset are released for academic use
  (add your own license if you publish the paper).
- **obfusCate** — GPL-3.0 (cloned by `setup.sh`).
- **llvm-pass-hikari** — based on lich4's OLLVM/Hikari project (cloned by
  `setup.sh`).
- **IDA Pro / Hex-Rays** are commercial products and are **not** redistributed;
  the decompilation stage requires a local installation.

---

*Pipeline used for the bachelor thesis «AI-Assisted Deobfuscation of C Code
Based on Large Language Models» (2026). If you use this work in a paper,
please cite it.*
