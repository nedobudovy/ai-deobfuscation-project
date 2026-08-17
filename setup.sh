#!/usr/bin/env bash
# setup.sh — fetch and build the two third-party tools used by the pipeline:
#
#   1. obfusCate          — source-level C obfuscator (GPL-3.0)
#        https://github.com/AlexJones0/obfusCate
#        used by obfuscate_source_code.sh to produce obfuscated_source_files/
#        (composition loaded from ./trans.cobf)
#
#   2. llvm-pass-hikari   — standalone OLLVM pass plugin (BCF / FLA / SUB)
#        https://github.com/lich4/llvm-pass-hikari
#        compiled with Homebrew LLVM into obfuscator/build/Obfuscator.dylib,
#        loaded by the Makefile via -fpass-plugin
#
# Commits are pinned to the exact revisions the study was run with.

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────
OBFUSCATE_REPO="https://github.com/AlexJones0/obfusCate"
OBFUSCATE_COMMIT="7b90fbe3198ea14d76367252acd957e3a8926936"

HIKARI_REPO="https://github.com/lich4/llvm-pass-hikari"
HIKARI_COMMIT="d9fab4eb01814f928beaece02cb70432dcd55ffa"

LLVM_HOME="${LLVM_HOME:-/opt/homebrew/opt/llvm}"
# ────────────────────────────────────────────────────────────────────────────

command -v git >/dev/null || { echo "ERROR: git not found"; exit 1; }
command -v cmake >/dev/null || { echo "ERROR: cmake not found"; exit 1; }
command -v ninja >/dev/null || { echo "ERROR: ninja not found"; exit 1; }

# Python + pip: prefer an active venv or the repo-local .venv (Homebrew's
# system Python refuses pip installs since PEP 668).
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
elif [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="$(command -v python3 || echo python)"
    echo "WARNING: no venv detected — using $PYTHON."
    echo "         If pip refuses to install (PEP 668, Homebrew Python), run:"
    echo "         python3 -m venv .venv && source .venv/bin/activate && ./setup.sh"
fi

if [ ! -x "$LLVM_HOME/bin/clang" ]; then
    echo "ERROR: Homebrew LLVM not found at $LLVM_HOME"
    echo "  Install with: brew install llvm cmake ninja"
    exit 1
fi

# This repo lives on a volume that emits AppleDouble (._*) junk files, which
# corrupt git pack indexes if they land inside .git. Clean them out.
scrub() { find "$1" -name '._*' -delete 2>/dev/null; git -C "$1" gc --quiet 2>/dev/null || true; }

# ── 1. obfusCate ───────────────────────────────────────────────────────────
if [ ! -d obfusCate ]; then
    echo "==> Cloning obfusCate"
    git clone "$OBFUSCATE_REPO"
    git -C obfusCate checkout --quiet "$OBFUSCATE_COMMIT"
else
    echo "==> obfusCate/ already present — skipping clone"
fi
scrub obfusCate

# The study ran with a few local patches on top of the pinned upstream commit
# (macOS include path for pycparser, a void-parameter crash fix, hex-literal
# support in integer encoding, fd_set typedef). Re-apply them.
echo "==> Applying obfusCate macOS fixes"
for p in patches/obfusCate/*.patch; do
    [ -f "$p" ] || continue
    git -C obfusCate apply --check "$(pwd)/$p" 2>/dev/null \
        && git -C obfusCate apply "$(pwd)/$p" \
        && echo "    applied $p"
done

echo "==> Installing obfusCate Python dependencies"
"$PYTHON" -m pip install -r obfusCate/requirements.txt

echo "==> Verifying obfusCate CLI"
"$PYTHON" obfusCate/obf_cli.py --help >/dev/null 2>&1 || true

# ── 2. llvm-pass-hikari plugin ─────────────────────────────────────────────
if [ ! -d llvm-pass-hikari ]; then
    echo "==> Cloning llvm-pass-hikari"
    git clone "$HIKARI_REPO"
    git -C llvm-pass-hikari checkout --quiet "$HIKARI_COMMIT"
else
    echo "==> llvm-pass-hikari/ already present — skipping clone"
fi
scrub llvm-pass-hikari

# Local patches used by the study on top of the pinned upstream commit:
# a BCF crash fix and registering the pass on the optimizer-last extension
# point (needed for it to run under clang's default -O0 pipeline).
echo "==> Applying llvm-pass-hikari fixes"
for p in patches/llvm-pass-hikari/*.patch; do
    [ -f "$p" ] || continue
    git -C llvm-pass-hikari apply --check "$(pwd)/$p" 2>/dev/null \
        && git -C llvm-pass-hikari apply "$(pwd)/$p" \
        && echo "    applied $p"
done

PLUGIN="llvm-pass-hikari/obfuscator/build/Obfuscator.dylib"
if [ ! -f "$PLUGIN" ]; then
    echo "==> Building Obfuscator.dylib against $LLVM_HOME"
    cmake -S llvm-pass-hikari/obfuscator \
          -B llvm-pass-hikari/obfuscator/build \
          -G Ninja \
          -DCMAKE_BUILD_TYPE=Release \
          -DLLVM_DIR="$LLVM_HOME/lib/cmake/llvm" \
          -DCMAKE_CXX_COMPILER="$LLVM_HOME/bin/clang++"
    cmake --build llvm-pass-hikari/obfuscator/build
else
    echo "==> Plugin already built at $PLUGIN"
fi

echo
echo "setup complete."
echo "  obfusCate          : ./obfusCate"
echo "  OLLVM plugin       : $PLUGIN"
echo
echo "Next steps:"
echo "  cp config.yaml.example config.yaml   # add your API keys"
echo "  ./obfuscate_source_code.sh           # source-level obfuscation"
echo "  ./build_variants.sh                  # 16 build_* variant matrix"
echo "  ./decompile_all.sh                   # IDA Pro batch decompilation"
