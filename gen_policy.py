#!/usr/bin/env python3
"""
gen_policy.py — generates policy.json for llvm-pass-hikari (lich4 fork)
Usage: python3 gen_policy.py --src-dir ./source_files --out build_all/policy.json --fla --bcf --sub
"""

import argparse
import json
import os
import subprocess
import sys


def get_function_names(src_dir):
    """Extract all function names from .c files using nm or grep fallback."""
    funcs = []
    for fname in os.listdir(src_dir):
        if fname.startswith(".") or not fname.endswith(".c"):
            continue
        fpath = os.path.join(src_dir, fname)
        # Use ctags-style grep: find lines like `return_type func_name(`
        try:
            result = subprocess.run(
                ["grep", "-oP", r"^\w[\w\s\*]+\s+(\w+)\s*\(", fpath],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                # extract last word before '('
                name = line.split("(")[0].strip().split()[-1].strip("*")
                if name and name not in ("if", "while", "for", "switch", "return"):
                    funcs.append(name)
        except Exception:
            pass
    return list(set(funcs)) if funcs else [".*"]  # fallback: match all


def build_policy(src_dir, fla, sub, bcf, split):
    """Build the policy.json structure."""

    # Determine which passes are active
    passes = {}
    if fla:
        passes["enable_fla"] = True
    if sub:
        passes["enable_sub"] = True
        passes["sub_times"] = 1
    if bcf:
        passes["enable_bcf"] = True
        passes["bcf_prob"] = 50
        passes["bcf_times"] = 1
    if split:
        passes["enable_split"] = True
        passes["split_n"] = 2

    if not passes:
        print("Warning: no passes selected, policy.json will do nothing.", file=sys.stderr)

    # Resolve absolute src_dir
    src_dir_abs = os.path.abspath(src_dir)

    # Build policy map: one policy template named "obf"
    policy_map = {
        "obf": passes
    }

    # Build policies list: one entry per .c file, matching all functions via regex
    policies = []
    for fname in sorted(os.listdir(src_dir)):
        if fname.startswith(".") or fname.startswith("._"):
            continue  # skip hidden / AppleDouble (._file.c) entries
        if not fname.endswith(".c"):
            continue
        # Match the compiled source file path as regex (escape dots)
        file_pattern = os.path.join(src_dir_abs, fname).replace(".", r"\.")
        # Apply policy to all functions in the file
        policies.append({
            "module": file_pattern,
            "func": ".*",       # match all functions
            "policy": "obf"
        })

    policy = {
        "src_root": src_dir_abs,
        "policy_map": policy_map,
        "policies": policies
    }

    return policy


def main():
    parser = argparse.ArgumentParser(description="Generate policy.json for llvm-pass-hikari")
    parser.add_argument("--src-dir", required=True, help="Directory containing .c source files")
    parser.add_argument("--out", required=True, help="Output path for policy.json")
    parser.add_argument("--fla",   action="store_true", help="Enable control flow flattening")
    parser.add_argument("--sub",   action="store_true", help="Enable instruction substitution")
    parser.add_argument("--bcf",   action="store_true", help="Enable bogus control flow")
    parser.add_argument("--split", action="store_true", help="Enable basic block splitting")
    args = parser.parse_args()

    policy = build_policy(
        src_dir=args.src_dir,
        fla=args.fla,
        sub=args.sub,
        bcf=args.bcf,
        split=args.split
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(policy, f, indent=2)

    print(f"Written: {args.out}")
    print(f"Passes:  fla={args.fla} sub={args.sub} bcf={args.bcf} split={args.split}")
    print(f"Modules: {len(policy['policies'])} source file(s) matched")


if __name__ == "__main__":
    main()
