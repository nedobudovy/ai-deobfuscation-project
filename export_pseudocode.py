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
