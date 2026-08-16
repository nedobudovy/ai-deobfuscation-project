#!/usr/bin/env bash
# build_variants.sh — build the full 16-variant matrix used in the study.
#
# Eight OLLVM pass combinations x two source families (source_files/ and
# obfuscated_source_files/) => 16 build_* directories:
#
#   build_plain                + build_obfuscated_plain     (no passes)
#   build_fla                  + build_obfuscated_fla       (FLA)
#   build_sub                  + build_obfuscated_sub       (SUB)
#   build_bcf                  + build_obfuscated_bcf       (BCF)
#   build_fla_bcf              + build_obfuscated_fla_bcf   (FLA+BCF)
#   build_fla_sub              + build_obfuscated_fla_sub   (FLA+SUB)
#   build_sub_bcf              + build_obfuscated_sub_bcf   (SUB+BCF)
#   build_all                  + build_obfuscated_all       (FLA+SUB+BCF)
#
# Requires: llvm-pass-hikari plugin built (see setup.sh), clang from Homebrew.

set -euo pipefail

VARIANTS=(
    "NO_OBFUSCATION=1"   # plain
    "FLA=1"
    "SUB=1"
    "BCF=1"
    "FLA=1 BCF=1"
    "FLA=1 SUB=1"
    "SUB=1 BCF=1"
    "ALL=1"
)

for v in "${VARIANTS[@]}"; do
    echo
    echo "════════════════════════════════════════"
    echo "  make ${v}"
    echo "════════════════════════════════════════"
    make ${v}
done

echo
echo "All 16 variants built. Directories:"
ls -d build_* | sort
