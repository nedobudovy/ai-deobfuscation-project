"""Prompt templates for IDA pseudocode → DEOBFUSCATED clean C reconstruction.

Six strategies — all framed around the fact that the input pseudocode comes
from a DELIBERATELY OBFUSCATED binary:
  - simple           : minimal instruction, with obfuscation note
  - zero_shot        : full requirements + obfuscation primer, no examples
  - one_shot         : + 1 worked example
  - few_shot         : + 3 worked examples
  - chain_of_thought : explicit deobfuscation-reasoning steps
  - role_expert      : role-play persona ("you wrote this code, recover it")
"""

# ─── Obfuscation primer (shared by all detailed prompts) ─────────────────────

_OBFUSCATION_CONTEXT = """\
CRITICAL CONTEXT: The IDA pseudocode you receive comes from a DELIBERATELY OBFUSCATED binary. The original source code has been transformed using one or more of the following techniques (compiler-level via OLLVM, and/or source-level via macros and junk code):

1. BOGUS CONTROL FLOW (BCF) — Real code is wrapped in fake conditional branches using opaque predicates that are mathematically always true or always false, but which the compiler cannot prove. Common opaque predicates:
   • (x*x + x) % 2 == 0       (always true for integer x)
   • 7*y*y - 1 != x*x         (always true)
   • (a*a + a) & 1 == 0       (always true)
   The "else" branch contains junk/dead code that is never executed.
   → ACTION: RECOGNIZE opaque predicates. REMOVE the dead branch. Keep only the always-taken path.

2. CONTROL FLOW FLATTENING (FLA) — Natural control flow (if / while / for) is collapsed into a single state machine: a giant `while (1) { switch (state) { case 0: ... state = 5; break; case 1: ... } }` structure. State variables thread control between original basic blocks.
   → ACTION: RECOGNIZE the dispatch-loop pattern. TRACE the state-transition graph. RECONSTRUCT the original natural control flow (if / while / for / do-while).

3. INSTRUCTION SUBSTITUTION (SUB) — Simple arithmetic operators are replaced by longer bit-twiddling sequences that compute the same result. Common patterns:
   • a + b  →  (a ^ b) + 2*(a & b)     or     (a | b) + (a & b)
   • a - b  →  a + (~b) + 1            or     a + (-b)
   • a ^ b  →  (a | b) - (a & b)
   • a & b  →  (a + b) - (a | b)
   • a | b  →  (a & b) + (a ^ b)
   → ACTION: RECOGNIZE these bit-twiddling expressions. SIMPLIFY them back to the natural arithmetic operator.

4. SOURCE-LEVEL OBFUSCATION — At the C source level: variable names replaced with cryptic macros (#define A1B2 some_var), inserted dead-code blocks, integer constants replaced with elaborate arithmetic expressions ((0x1337 ^ 0xDEAD) - 12345 instead of 42), control flow scrambled with goto chains.
   → ACTION: STRIP junk; FOLD constant expressions back to natural literals; UNDO goto-chains where they are clearly artificial.

Your job is NOT to faithfully transcribe the pseudocode. Your job is to DEOBFUSCATE — recognize these patterns and recover the CLEAN, NATURAL C source code that the obfuscation was hiding. Do not preserve the obfuscation transformations; UNDO them. Produce the human-written C code that the program presumably looked like before obfuscation was applied."""


# ─── Standard output requirements (after the obfuscation primer) ─────────────

_REQUIREMENTS = """\
Requirements for the output:
- Output a COMPLETE, SELF-CONTAINED C source file that compiles with gcc without errors.
- Include ALL necessary #include directives (stdio.h, stdlib.h, string.h, etc.).
- Define ALL structs, global variables, and constants at the top of the file.
- Reconstruct a working main() function if the pseudocode contains one, or write a \
minimal main() stub that calls the visible functions so the file links correctly.
- Rename every IDA-generated variable (v1, v2, a1, a2 …) to a meaningful name \
based on how it is used.
- Replace all IDA-specific types: __int64 → long, _OWORD → a char array of the \
appropriate size, _BYTE → char, _DWORD → int, HIWORD/LOWORD → bitwise ops.
- Remove IDA annotations (// w8, // x21, BYREF, etc.).
- DEOBFUSCATE: undo BCF (remove fake branches), undo FLA (recover natural loops), \
undo SUB (simplify bit-twiddling back to + - ^ & |), strip junk dead code.
- The reconstructed C should reflect the program's TRUE behaviour, not its \
obfuscated implementation.
- Use standard C99. No compiler extensions, no C++.
- Output ONLY the C source code — no explanations, no markdown, no comments \
about what you changed."""


# ─── Worked examples (real pairs from this dataset) ──────────────────────────

EXAMPLE_1_PSEUDOCODE = """\
int __fastcall main(int argc, const char **argv, const char **envp)
{
  const char *v3; // x19

  if ( argc == 2 )
  {
    v3 = argv[1];
    if ( !strncmp(v3, "password1", 9u) )
    {
      printf("Yes, %s is correct!\\n", v3);
      return 0;
    }
    else
    {
      printf("No, %s is not correct.\\n", v3);
      return 1;
    }
  }
  else
  {
    puts("Need exactly one argument.");
    return -1;
  }
}"""

EXAMPLE_1_CLEAN = """\
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    if (argc != 2) {
        printf("Need exactly one argument.\\n");
        return -1;
    }

    const char *correct = "password1";

    if (strncmp(argv[1], correct, strlen(correct)) != 0) {
        printf("No, %s is not correct.\\n", argv[1]);
        return 1;
    } else {
        printf("Yes, %s is correct!\\n", argv[1]);
        return 0;
    }
}"""


EXAMPLE_2_PSEUDOCODE = """\
int __fastcall main(int argc, const char **argv, const char **envp)
{
  const char *v3; // x8

  if ( argc == 2 )
  {
    v3 = argv[1];
    if ( *v3
      && (*v3 != 111
       || v3[1]
       && (v3[1] != 96
        || v3[2]
        && (v3[2] != 114
         || v3[3]
         && (v3[3] != 114
          || v3[4]
          && (v3[4] != 118
           || v3[5] && (v3[5] != 110 || v3[6] && (v3[6] != 113 || v3[7] && (v3[7] != 99 || v3[8] && v3[8] != 48)))))))) )
    {
      printf("No, %s is not correct.\\n", argv[1]);
      return 1;
    }
    else
    {
      printf("Yes, %s is correct!\\n", argv[1]);
      return 0;
    }
  }
  else
  {
    puts("Need exactly one argument.");
    return -1;
  }
}"""

EXAMPLE_2_CLEAN = """\
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    if (argc != 2) {
        printf("Need exactly one argument.\\n");
        return -1;
    }

    /* The hardcoded character checks (111, 96, 114, 114, 118, 110, 113, 99, 48)
       are each character of "password1" minus 1 — the compiler unrolled a
       character-by-character comparison loop. Reconstruct the loop.  */
    const char *correct = "password1";
    int i = 0;
    while (correct[i] != 0 && argv[1][i] != 0) {
        if ((correct[i] - 1) != argv[1][i]) {
            printf("No, %s is not correct.\\n", argv[1]);
            return 1;
        }
        i++;
    }
    printf("Yes, %s is correct!\\n", argv[1]);
    return 0;
}"""


# Example 3 — illustrates undoing source-level obfuscation: junk variables,
# IDA _OWORD shuffle, and reconstructing an array-of-structs.
EXAMPLE_3_PSEUDOCODE = """\
__int64 addRecord()
{
  int v1; // w8
  _OWORD *v2; // x8
  __int128 v3; // q1
  _OWORD v4[2]; // [xsp+Ch] [xbp-64h] BYREF
  _BYTE v5[28]; // [xsp+2Ch] [xbp-44h] BYREF

  if ( recordCount >= 100 )
    return puts("Cannot add more records. The list is full.");
  printf("Enter ID: ");
  scanf("%d", v4);
  printf("Enter Name: ");
  scanf("%s", (char *)v4 + 4);
  printf("Enter 1 for Present or 0 for Absent: ");
  scanf("%d", &v5[24]);
  v1 = recordCount++;
  v2 = (_OWORD *)((char *)&records + 60 * v1);
  v3 = v4[1];
  *v2 = v4[0];
  v2[1] = v3;
  return puts("Record added successfully!");
}"""

EXAMPLE_3_CLEAN = """\
#include <stdio.h>
#include <string.h>

#define MAX_RECORDS 100

typedef struct {
    int  id;
    char name[52];
    int  present;
} Record;

Record records[MAX_RECORDS];
int    recordCount = 0;

void addRecord(void)
{
    if (recordCount >= MAX_RECORDS) {
        puts("Cannot add more records. The list is full.");
        return;
    }
    int  new_id;
    char new_name[52];
    int  new_present;

    printf("Enter ID: ");
    scanf("%d", &new_id);
    printf("Enter Name: ");
    scanf("%51s", new_name);
    printf("Enter 1 for Present or 0 for Absent: ");
    scanf("%d", &new_present);

    records[recordCount].id      = new_id;
    strncpy(records[recordCount].name, new_name, sizeof(records[recordCount].name) - 1);
    records[recordCount].present = new_present;
    recordCount++;
    puts("Record added successfully!");
}

int main(void)
{
    addRecord();
    return 0;
}"""


def _block(pseudo: str, clean: str) -> str:
    return (
        "### OBFUSCATED IDA pseudocode input:\n"
        f"{pseudo}\n\n"
        "### Correct DEOBFUSCATED C output:\n"
        f"{clean}"
    )


ONE_SHOT_EXAMPLES  = _block(EXAMPLE_1_PSEUDOCODE, EXAMPLE_1_CLEAN)
FEW_SHOT_EXAMPLES  = "\n\n---\n\n".join([
    _block(EXAMPLE_1_PSEUDOCODE, EXAMPLE_1_CLEAN),
    _block(EXAMPLE_2_PSEUDOCODE, EXAMPLE_2_CLEAN),
    _block(EXAMPLE_3_PSEUDOCODE, EXAMPLE_3_CLEAN),
])


# ─── Prompt templates ────────────────────────────────────────────────────────

PROMPTS = {
    # ── 1. Simple — minimal but obfuscation-aware ────────────────────────────
    "simple": {
        "system": (
            "You are a reverse engineer. The IDA Pro pseudocode you receive comes "
            "from an OBFUSCATED binary (bogus control flow, control flow flattening, "
            "instruction substitution). Recover the clean original C source."
        ),
        "user": (
            "This IDA pseudocode is from an OBFUSCATED binary. Recover the original "
            "clean C source — undo opaque predicates, undo flattened state machines, "
            "and simplify bit-twiddled arithmetic. Output only the C code.\n\n"
            "{pseudocode}"
        ),
    },

    # ── 2. Zero-shot — obfuscation primer + full requirements ────────────────
    "zero_shot": {
        "system": (
            "You are an expert reverse engineer and C programmer specializing in "
            "deobfuscation. Your task is to recover the ORIGINAL clean C source "
            "from IDA Pro Hex-Rays pseudocode of a deliberately OBFUSCATED binary.\n\n"
            + _OBFUSCATION_CONTEXT + "\n\n"
            + _REQUIREMENTS
        ),
        "user": (
            "Below is IDA Pro Hex-Rays pseudocode extracted from a binary that has "
            "been obfuscated (BCF, FLA, SUB, or source-level transformations). "
            "Recover the original clean C source code — do NOT transcribe the "
            "obfuscation, UNDO it.\n\n"
            "{pseudocode}\n\n"
            "Output ONLY the deobfuscated C source code. No markdown, no explanations."
        ),
    },

    # ── 3. One-shot — primer + requirements + 1 example ──────────────────────
    "one_shot": {
        "system": (
            "You are an expert reverse engineer and C programmer specializing in "
            "deobfuscation. Your task is to recover the ORIGINAL clean C source "
            "from IDA Pro Hex-Rays pseudocode of a deliberately OBFUSCATED binary.\n\n"
            + _OBFUSCATION_CONTEXT + "\n\n"
            + _REQUIREMENTS
        ),
        "user": (
            "Study the example below — see how obfuscation patterns in the pseudocode "
            "get UNDONE in the clean output — then apply the same approach to the new "
            "obfuscated pseudocode.\n\n"
            "## EXAMPLE\n"
            "{one_shot_example}\n\n"
            "## NOW YOUR TASK — OBFUSCATED IDA pseudocode input:\n"
            "{pseudocode}\n\n"
            "Output ONLY the deobfuscated C source code. No markdown, no explanations."
        ),
    },

    # ── 4. Few-shot — primer + requirements + 3 examples ─────────────────────
    "few_shot": {
        "system": (
            "You are an expert reverse engineer and C programmer specializing in "
            "deobfuscation. Your task is to recover the ORIGINAL clean C source "
            "from IDA Pro Hex-Rays pseudocode of a deliberately OBFUSCATED binary.\n\n"
            + _OBFUSCATION_CONTEXT + "\n\n"
            + _REQUIREMENTS
        ),
        "user": (
            "Study the three examples below (simple, medium with unrolled loop, "
            "and complex with junk variables) — see how the obfuscated pseudocode "
            "is UNDONE to recover the clean original — then apply the same approach "
            "to the new obfuscated pseudocode.\n\n"
            "## EXAMPLES\n"
            "{few_shot_examples}\n\n"
            "## NOW YOUR TASK — OBFUSCATED IDA pseudocode input:\n"
            "{pseudocode}\n\n"
            "Output ONLY the deobfuscated C source code. No markdown, no explanations."
        ),
    },

    # ── 5. Chain-of-thought — explicit deobfuscation reasoning ───────────────
    "chain_of_thought": {
        "system": (
            "You are an expert reverse engineer and C programmer specializing in "
            "deobfuscation."
        ),
        "user": (
            "The IDA pseudocode below comes from a deliberately OBFUSCATED binary. "
            "Recover the original clean C source.\n\n"
            "Work through these deobfuscation steps internally before writing the code:\n\n"
            "  1. SCAN for obfuscation patterns:\n"
            "     - Opaque predicates (BCF): expressions in if-conditions that are "
            "always-true/always-false but the compiler couldn't prove. Examples: "
            "(x*x+x)%2==0, 7*y*y-1!=x*x. The 'else' branch is junk.\n"
            "     - Flattened state machines (FLA): while(1){{switch(state){{...}}}} where "
            "state is updated inside cases. This is a flattened natural loop.\n"
            "     - Substituted arithmetic (SUB): bit-twiddling like (a^b)+2*(a&b) "
            "for a+b, (a|b)-(a&b) for a^b.\n\n"
            "  2. UNDO each pattern detected:\n"
            "     - Remove BCF dead branches; keep only the real path.\n"
            "     - Trace FLA state transitions and reconstruct the natural "
            "for/while/if structure.\n"
            "     - Simplify SUB expressions back to natural + - ^ & | operators.\n\n"
            "  3. RECOVER intent:\n"
            "     - Identify what each function does (purpose, inputs, outputs).\n"
            "     - Infer data structures from memory layout.\n"
            "     - Choose meaningful names for all IDA variables (v1, v2, a1 …).\n"
            "     - Map IDA types to standard C: __int64→long, _OWORD→char[], "
            "_BYTE→char, _DWORD→int.\n\n"
            "  4. ASSEMBLE the deobfuscated .c file: includes, structs, globals, "
            "functions, and a main().\n\n"
            + _REQUIREMENTS + "\n\n"
            "OBFUSCATED IDA pseudocode:\n"
            "{pseudocode}\n\n"
            "Output ONLY the deobfuscated C source code. No markdown, no explanations."
        ),
    },

    # ── 6. Role / persona ────────────────────────────────────────────────────
    "role_expert": {
        "system": (
            "You are a senior C systems programmer with 15 years of reverse-engineering "
            "experience. Someone has obfuscated a binary you originally wrote (using "
            "OLLVM's bogus control flow, control flow flattening, and instruction "
            "substitution passes — plus some source-level macro junk). You remember "
            "exactly what the program does. Your job is to undo the obfuscation and "
            "restore the clean, original source code so it compiles and runs correctly "
            "again — not to transcribe the obfuscation.\n\n"
            + _OBFUSCATION_CONTEXT + "\n\n"
            + _REQUIREMENTS
        ),
        "user": (
            "Here is the IDA decompilation of your obfuscated binary. Undo the "
            "obfuscation transformations (BCF, FLA, SUB, junk code) and restore "
            "the original clean C source file:\n\n"
            "{pseudocode}\n\n"
            "Output ONLY the deobfuscated C source code. No markdown, no explanations."
        ),
    },
}


# ─── Builder ─────────────────────────────────────────────────────────────────

def build_messages(prompt_name: str, pseudocode: str) -> list[dict]:
    """Build the messages list for a given prompt type and pseudocode."""
    template = PROMPTS[prompt_name]
    user_text = template["user"].format(
        pseudocode=pseudocode,
        one_shot_example=ONE_SHOT_EXAMPLES,
        few_shot_examples=FEW_SHOT_EXAMPLES,
    )
    return [
        {"role": "system", "content": template["system"]},
        {"role": "user", "content": user_text},
    ]
