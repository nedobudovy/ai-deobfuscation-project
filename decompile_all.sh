#!/bin/bash
# decompile_all.sh - Batch IDA Pro decompiler for all build_* folders

set -u

# ── CONFIG ──────────────────────────────────────────────────────────────────
# Override with: IDA_PATH=/path/to/idat ./decompile_all.sh
IDA_PATH="${IDA_PATH:-/Applications/IDA Professional 9.2.app/Contents/MacOS/idat}"

# Automatically include every build_* directory
BUILD_DIRS=($(find . -maxdepth 1 -type d -name "build_*" | sed 's|^\./||' | sort))

OUT_ROOT="decompiled"
LOG_DIR="/tmp/ida_decompile_logs"
IDAPYTHON_SCRIPT="$(pwd)/export_pseudocode.py"
# ────────────────────────────────────────────────────────────────────────────

echo "════════════════════════════════════════"
echo "IDA Batch Decompiler"
echo "════════════════════════════════════════"
echo ""

# Verify IDA exists
if [ ! -f "$IDA_PATH" ]; then
    echo "ERROR: IDA not found:"
    echo "  $IDA_PATH"
    echo ""
    echo "Available IDA binaries:"
    find /Applications -iname "idat*" 2>/dev/null
    exit 1
fi

# Verify build directories exist
if [ ${#BUILD_DIRS[@]} -eq 0 ]; then
    echo "ERROR: No build_* directories found."
    exit 1
fi

echo "Build directories:"
for dir in "${BUILD_DIRS[@]}"; do
    echo "  - $dir"
done
echo ""

mkdir -p "$LOG_DIR"
mkdir -p "$OUT_ROOT"

# ── IDA PYTHON SCRIPT ─────────────────────────────────────────────────────
# export_pseudocode.py is checked into the repo; fall back to an inline
# copy if someone deleted it.
if [ ! -f "$IDAPYTHON_SCRIPT" ]; then
    cat > "$IDAPYTHON_SCRIPT" << 'PYEOF'
import os
import idc
import ida_auto
import ida_hexrays
import ida_name
import idautils

def main():
    output_path = os.environ.get("IDA_OUTPUT_PATH")

    if not output_path:
        idc.msg("ERROR: IDA_OUTPUT_PATH not set\n")
        idc.qexit(1)

    ida_auto.auto_wait()

    if not ida_hexrays.init_hexrays_plugin():
        idc.msg("ERROR: Hex-Rays decompiler unavailable\n")
        idc.qexit(2)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    success = 0
    failed = 0

    with open(output_path, "w", encoding="utf-8", errors="replace") as f:
        f.write("// Decompiled with IDA Pro Hex-Rays\n")
        f.write(f"// Input: {idc.get_input_file_path()}\n\n")

        for func_ea in idautils.Functions():
            try:
                func_name = ida_name.get_name(func_ea)

                if not func_name:
                    func_name = f"sub_{func_ea:x}"

                cfunc = ida_hexrays.decompile(func_ea)

                if cfunc:
                    f.write(f"// ─────────────────────────────────────\n")
                    f.write(f"// Function: {func_name}\n")
                    f.write(f"// Address : 0x{func_ea:x}\n")
                    f.write(f"// ─────────────────────────────────────\n\n")

                    f.write(str(cfunc))
                    f.write("\n\n")

                    success += 1

            except Exception as e:
                f.write(f"// FAILED: {func_name} @ 0x{func_ea:x}\n")
                f.write(f"// Error: {e}\n\n")
                failed += 1

    idc.msg(f"Done: {success} success, {failed} failed\n")
    idc.qexit(0)

main()
PYEOF
    echo "IDA Python helper written:"
    echo "  $IDAPYTHON_SCRIPT"
    echo ""
fi

# ── COUNT BINARIES ─────────────────────────────────────────────────────────
TOTAL=0

for build_dir in "${BUILD_DIRS[@]}"; do
    while IFS= read -r -d '' binary; do
        if file "$binary" | grep -q "Mach-O"; then
            ((TOTAL++))
        fi
    done < <(find "$build_dir" -type f -print0)
done

echo "Found $TOTAL Mach-O binaries"
echo ""

if [ "$TOTAL" -eq 0 ]; then
    echo "No binaries found."
    exit 0
fi

# ── PROCESS ────────────────────────────────────────────────────────────────
DONE=0
SKIPPED=0
ERRORS=0

for build_dir in "${BUILD_DIRS[@]}"; do

    while IFS= read -r -d '' binary; do

        if ! file "$binary" | grep -q "Mach-O"; then
            continue
        fi

        relative_path="${binary#./}"
        filename=$(basename "$binary")

        output_file="$OUT_ROOT/${relative_path}.c"
        log_name=$(echo "${relative_path}" | tr '/' '_')
        log_file="$LOG_DIR/${log_name}.log"

        ((DONE++))

        printf "[%d/%d] %s ... " "$DONE" "$TOTAL" "$relative_path"

        # Skip existing non-empty outputs
        if [ -s "$output_file" ]; then
            echo "SKIP"
            ((SKIPPED++))
            continue
        fi

        mkdir -p "$(dirname "$output_file")"

        export IDA_OUTPUT_PATH="$(pwd)/$output_file"

        "$IDA_PATH" \
            -A \
            -c \
            -S"$IDAPYTHON_SCRIPT" \
            -L"$log_file" \
            "$binary" \
            > /dev/null 2>&1

        exit_code=$?

        # Cleanup IDA temporary files
        rm -f \
            "${binary}.id0" \
            "${binary}.id1" \
            "${binary}.id2" \
            "${binary}.nam" \
            "${binary}.til" \
            "${binary}.idb" \
            "${binary}.i64"

        if [ -s "$output_file" ]; then
            lines=$(wc -l < "$output_file")
            echo "OK (${lines} lines)"
        else
            echo "FAIL (exit=$exit_code)"
            ((ERRORS++))

            if [ -f "$log_file" ]; then
                tail -5 "$log_file" | sed 's/^/    > /'
            fi
        fi

    done < <(find "$build_dir" -type f -print0)

done

# ── SUMMARY ────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "Finished"
echo "════════════════════════════════════════"
echo "Processed : $DONE"
echo "Skipped   : $SKIPPED"
echo "Errors    : $ERRORS"
echo ""
echo "Output:"
echo "  $OUT_ROOT/"
echo ""
echo "Logs:"
echo "  $LOG_DIR/"
echo ""

if [ "$ERRORS" -gt 0 ]; then
    echo "Failed outputs:"
    find "$OUT_ROOT" -name "*.c" -size 0 2>/dev/null
fi
