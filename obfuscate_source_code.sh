#!/usr/bin/env bash
# obfuscate_source_code.sh — source-level obfuscation of every program in
# source_files/ using the obfusCate CLI. Output goes to obfuscated_source_files/.
#
# The transformation composition (trans.cobf) defines:
#   Integer Literal Encoding, Function Interface Randomisation,
#   Opaque Predicate Augmentation, String Literal Encoding,
#   Identifier Renaming (complete random).
#
# Note: obfusCate resolves its fake-libc include dir relative to the working
# directory, so the CLI is executed with CWD inside ./obfusCate. All input and
# output paths are made absolute to stay repo-root relative.
#
# Requires: ./obfusCate checkout with Python deps installed (see setup.sh).

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

SOURCE_DIR="$REPO_ROOT/source_files"
OUTPUT_DIR="$REPO_ROOT/obfuscated_source_files"
OBFUSCATE_DIR="$REPO_ROOT/obfusCate"

# trans.cobf ships at repo root; fall back to the clone-local copy.
COMPOSITION="$REPO_ROOT/trans.cobf"
[ -f "$COMPOSITION" ] || COMPOSITION="$OBFUSCATE_DIR/trans.cobf"

if [ ! -f "$OBFUSCATE_DIR/obf_cli.py" ]; then
    echo "ERROR: $OBFUSCATE_DIR/obf_cli.py not found — run ./setup.sh first."
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

shopt -s nullglob
files=("$SOURCE_DIR"/*.c)

if [ ${#files[@]} -eq 0 ]; then
    echo "Warning: No .c files found in $SOURCE_DIR"
    exit 0
fi

# Run from inside obfusCate so the fake-libc include path resolves.
cd "$OBFUSCATE_DIR"

# Prefer the venv python (Homebrew's system python refuses pip installs)
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
else
    PYTHON="python"
fi

for src in "${files[@]}"; do
    filename=$(basename "$src")
    output="$OUTPUT_DIR/obfuscated_$filename"
    echo "Processing: $src → $output"
    "$PYTHON" obf_cli.py "$src" "$output" -l "$COMPOSITION" -s
done

echo "Done. All files processed."
