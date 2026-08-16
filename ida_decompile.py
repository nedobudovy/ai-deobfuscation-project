"""
ida_decompile.py — headless IDA Pro Hex-Rays decompilation of a single binary.

Reused by the Streamlit deobfuscation app. Wraps the same invocation that
decompile_all.sh uses, but for one file and returning the pseudocode as a string.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

# Default IDA path (matches decompile_all.sh). Override via IDA_PATH env var.
DEFAULT_IDA_PATH = "/Applications/IDA Professional 9.2.app/Contents/MacOS/idat"

# IDAPython script that dumps Hex-Rays pseudocode of every function to
# the path in IDA_OUTPUT_PATH.
_IDA_SCRIPT = r'''
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
                func_name = ida_name.get_name(func_ea) or f"sub_{func_ea:x}"
                cfunc = ida_hexrays.decompile(func_ea)
                if cfunc:
                    f.write("// ──────────\n")
                    f.write(f"// Function: {func_name}\n")
                    f.write(f"// Address : 0x{func_ea:x}\n")
                    f.write("// ──────────\n\n")
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
'''


class IDAError(Exception):
    pass


def find_ida() -> str:
    """Locate the IDA `idat` binary."""
    env = os.environ.get("IDA_PATH")
    if env and Path(env).exists():
        return env
    if Path(DEFAULT_IDA_PATH).exists():
        return DEFAULT_IDA_PATH
    # Last resort: search /Applications
    import glob
    for cand in glob.glob("/Applications/**/idat*", recursive=True):
        if Path(cand).is_file():
            return cand
    raise IDAError(
        "IDA Pro not found. Set the IDA_PATH environment variable to the "
        "full path of the `idat` binary (e.g. "
        "'/Applications/IDA Professional 9.2.app/Contents/MacOS/idat')."
    )


def decompile_binary(binary_path: str, timeout: int = 300) -> str:
    """
    Run IDA Pro headless on `binary_path`, return Hex-Rays pseudocode as a string.

    Raises IDAError on failure.
    """
    ida = find_ida()
    binary_path = str(Path(binary_path).resolve())

    work = tempfile.mkdtemp(prefix="ida_app_")
    script_path = os.path.join(work, "export_pseudocode.py")
    output_path = os.path.join(work, "pseudocode.c")
    log_path = os.path.join(work, "ida.log")

    Path(script_path).write_text(_IDA_SCRIPT, encoding="utf-8")

    env = dict(os.environ)
    env["IDA_OUTPUT_PATH"] = output_path

    try:
        proc = subprocess.run(
            [ida, "-A", "-c", f"-S{script_path}", f"-L{log_path}", binary_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise IDAError(f"IDA timed out after {timeout}s") from e

    # Cleanup IDA database artifacts
    for ext in (".id0", ".id1", ".id2", ".nam", ".til", ".idb", ".i64"):
        try:
            os.remove(binary_path + ext)
        except OSError:
            pass

    if not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
        log_tail = ""
        if Path(log_path).exists():
            log_tail = "\n".join(Path(log_path).read_text(errors="replace").splitlines()[-10:])
        raise IDAError(
            f"IDA produced no pseudocode (exit={proc.returncode}).\n"
            f"Log tail:\n{log_tail}"
        )

    return Path(output_path).read_text(encoding="utf-8", errors="replace")
