"""
deobf_evaluator.py — автоматична оцінка якості реконструкції C-коду
за рубрикою F + S + R (кожна 0–2, сума 0–6).

F — Функціональність  (компілюється / smoke-test / I/O тести)
S — Структурна схожість з оригіналом  (CC, кількість функцій, AST)
R — Читабельність  (IDA-артефакти, осмисленість імен, тип-залишки)
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import lizard
from tree_sitter import Language, Parser
import tree_sitter_c

C_LANG = Language(tree_sitter_c.language())
_parser = Parser(C_LANG)


# ─────────────────────────────────────────────────────────────────────────────
# Stdlib / language identifier filter (used by readability score)
# ─────────────────────────────────────────────────────────────────────────────
# These should NOT count toward meaningful_ratio — they're always present
# in any C program and inflate the score regardless of the model's renaming
# effort on actual program variables.

_STDLIB_NAMES = frozenset({
    # stdio
    "printf", "fprintf", "sprintf", "snprintf", "vprintf", "vfprintf", "vsprintf",
    "scanf", "fscanf", "sscanf", "puts", "fputs", "gets", "fgets",
    "putchar", "getchar", "fputc", "fgetc", "putc", "getc", "ungetc",
    "fopen", "fclose", "freopen", "fread", "fwrite", "fseek", "ftell",
    "rewind", "fflush", "feof", "ferror", "clearerr", "perror",
    "stdin", "stdout", "stderr", "FILE", "EOF", "NULL", "size_t", "ssize_t",
    # stdlib
    "malloc", "calloc", "realloc", "free", "exit", "abort", "atexit",
    "atoi", "atol", "atoll", "atof", "strtol", "strtoul", "strtod", "strtoll",
    "rand", "srand", "qsort", "bsearch", "abs", "labs", "div", "ldiv",
    "system", "getenv", "setenv",
    # string
    "strlen", "strcpy", "strncpy", "strcat", "strncat", "strcmp", "strncmp",
    "strchr", "strrchr", "strstr", "strtok", "strdup", "strndup", "strerror",
    "memcpy", "memmove", "memset", "memcmp", "memchr",
    # ctype
    "isalpha", "isdigit", "isalnum", "isspace", "ispunct", "isupper", "islower",
    "isxdigit", "isprint", "iscntrl", "isgraph", "toupper", "tolower",
    # math
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sqrt", "pow", "exp", "log", "log2", "log10",
    "floor", "ceil", "round", "fmod", "fabs", "trunc",
    "INFINITY", "NAN", "M_PI", "M_E",
    # time
    "time", "clock", "difftime", "mktime", "localtime", "gmtime",
    "strftime", "asctime", "ctime", "time_t", "clock_t", "tm",
    # assert / errno
    "assert", "errno",
    # stdint
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "intptr_t", "uintptr_t", "ptrdiff_t",
    "UINT8_MAX", "UINT16_MAX", "UINT32_MAX", "UINT64_MAX",
    "INT_MAX", "INT_MIN", "UINT_MAX", "LONG_MAX", "LONG_MIN",
    # stdbool / true/false
    "bool", "true", "false",
    # main + signatures
    "main", "argc", "argv", "envp",
    # unistd / posix (commonly seen)
    "open", "close", "read", "write", "lseek", "dup", "dup2", "pipe",
    "fork", "exec", "execl", "execv", "execve", "wait", "waitpid",
    "getpid", "getuid", "getgid", "sleep", "usleep", "kill", "signal",
})


def _letter_entropy(name: str) -> float:
    """Shannon entropy of letter frequency. 'aaaa' → 0, 'abcd' → 2.0, 'buffer' → 2.25."""
    letters = [c.lower() for c in name if c.isalpha()]
    if len(letters) < 2:
        return 0.0
    freq = Counter(letters)
    n = len(letters)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


# ─────────────────────────────────────────────────────────────────────────────
# Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EvalResult:
    F: int        # 0–2  функціональність
    S: int        # 0–2  структурна схожість
    R: int        # 0–2  читабельність
    total: int    # 0–6
    grade: int    # 0–5  (шкала диплому)
    details: dict


# ─────────────────────────────────────────────────────────────────────────────
# Утиліти
# ─────────────────────────────────────────────────────────────────────────────

def strip_markdown(text: str) -> str:
    """Витягує C-код з ```c … ``` блоків; якщо блоків нема — повертає як є."""
    matches = re.findall(r"```(?:c|cpp)?\s*\n(.*?)```", text, re.DOTALL)
    return "\n".join(matches) if matches else text


def _parse(src: str):
    return _parser.parse(src.encode(errors="replace"))


def _walk_nodes(node):
    yield node
    for child in node.children:
        yield from _walk_nodes(child)


def extract_identifiers(src: str) -> list[str]:
    tree = _parse(src)
    return [n.text.decode(errors="replace")
            for n in _walk_nodes(tree.root_node)
            if n.type == "identifier"]


def count_functions_ast(src: str) -> int:
    """Кількість function_definition вузлів в AST."""
    tree = _parse(src)
    return sum(1 for n in _walk_nodes(tree.root_node)
               if n.type == "function_definition")


# ─────────────────────────────────────────────────────────────────────────────
# F — Функціональність
# ─────────────────────────────────────────────────────────────────────────────

def _compile(src_text: str) -> tuple[bool, str, str, int]:
    """
    Компілює src_text. Повертає (ok, binary_path_or_'', stderr, warning_count).
    Використовує стрикгі прапори: -Wall -Wextra -Werror=implicit-function-declaration
    -Werror=int-conversion (без цих хард-фейлів модель могла б повернути код з
    implicit decl і отримати F=2 хоча реально це баг).
    """
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "m.c")
    out = os.path.join(tmp, "m.bin")
    Path(src).write_text(src_text, encoding="utf-8")
    r = subprocess.run(
        ["gcc", "-O0", "-Wall", "-Wextra",
         "-Werror=implicit-function-declaration",
         "-Werror=int-conversion",
         "-Werror=incompatible-pointer-types",
         "-lm", src, "-o", out],
        capture_output=True, text=True, timeout=30,
    )
    warn_count = len(re.findall(r": warning:", r.stderr))
    if r.returncode == 0:
        return True, out, r.stderr, warn_count
    return False, "", r.stderr, warn_count


# Канонічні stdin-послідовності для behavioural-test.
# Покривають типові патерни: empty / one-shot / menu navigation / exit-token.
_CANNED_INPUTS = ["", "1\n", "0\n", "1\n0\n", "test\n0\n", "1\n1\n0\n0\n"]

# returncode які означають крах
_CRASH_RETURNCODES = frozenset({-11, -6, 134, 139, -4, 132, 136})


def _normalize_stdout(s: str) -> str:
    """Нормалізує stdout для порівняння: lowercase, без зайвих пробілів і пунктуації."""
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s.strip()


def _run_with_input(binary: str, stdin_data: str, timeout: int = 3) -> tuple[int, str]:
    """Запускає бінарник з заданим stdin. Повертає (returncode, stdout)."""
    try:
        r = subprocess.run(
            [binary], input=stdin_data,
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        return -2, ""    # умовний код для таймауту
    except Exception:
        return -3, ""


def _behavioural_compare(orig_src: str, model_src: str) -> tuple[float, dict]:
    """
    Компілює оригінал ТА модель, запускає обидва на однакових stdin-послідовностях,
    повертає (match_ratio ∈ [0,1], details).

    match_ratio = частка тестів де нормалізований stdout збігається.
    Якщо оригінал не компілюється — fallback до smoke-test на моделі.

    Швидкий шлях: якщо src ідентичні (self-check) — пропускаємо подвійні прогони.
    """
    if orig_src == model_src:
        ok, _, _, _ = _compile(orig_src)
        return (1.0 if ok else 0.0), {
            "original_compiled": ok, "model_compiled": ok,
            "behavioural_matches": len(_CANNED_INPUTS),
            "behavioural_total":   len(_CANNED_INPUTS),
            "model_crashes": 0,
            "shortcut": "identical_sources",
        }

    ok_o, bin_o, _, _ = _compile(orig_src)
    if not ok_o:
        return -1.0, {"original_compiled": False, "reason": "cannot compare (orig fails to compile)"}

    ok_m, bin_m, _, _ = _compile(model_src)
    if not ok_m:
        return 0.0, {"original_compiled": True, "model_compiled": False}

    matches = 0
    model_crashes = 0
    per_test = []
    for inp in _CANNED_INPUTS:
        rc_o, out_o = _run_with_input(bin_o, inp)
        rc_m, out_m = _run_with_input(bin_m, inp)
        if rc_m in _CRASH_RETURNCODES:
            model_crashes += 1
            per_test.append({"input": repr(inp), "match": False, "reason": "model_crash"})
            continue
        match = _normalize_stdout(out_o) == _normalize_stdout(out_m)
        if match:
            matches += 1
        per_test.append({"input": repr(inp), "match": match})

    total = len(_CANNED_INPUTS)
    return matches / total, {
        "original_compiled": True,
        "model_compiled": True,
        "behavioural_matches": matches,
        "behavioural_total": total,
        "model_crashes": model_crashes,
        "per_test": per_test,
    }


def _run_io_tests(binary: str, tests: list[dict]) -> tuple[int, int]:
    """I/O тести (зовнішні). tests = [{"input": "...", "expected": "..."}]."""
    passed = 0
    for t in tests:
        try:
            r = subprocess.run(
                [binary], input=t["input"],
                capture_output=True, text=True, timeout=5,
            )
            if r.stdout.strip() == t["expected"].strip():
                passed += 1
        except subprocess.TimeoutExpired:
            pass
    return passed, len(tests)


def score_F(
    model_src: str,
    orig_src: str | None = None,
    tests: list[dict] | None = None,
) -> tuple[int, dict]:
    """
    F (функціональність):
      0 — модель не компілюється
      1 — компілюється; поведінкова збіжність < 50% (або I/O < 50%)
      2 — компілюється + поведінкова збіжність ≥ 50%

    Пріоритет вхідних сигналів:
      a) явні I/O тести (якщо передані)
      b) behavioural-compare з оригіналом (якщо orig_src переданий і компілюється)
      c) fallback: legacy smoke-test (не падає на EOF stdin)
    """
    try:
        ok, binary, err, warnings = _compile(model_src)
    except Exception as e:
        return 0, {"compiled": False, "error": str(e)[:200]}

    if not ok:
        return 0, {"compiled": False, "error": err[:300]}

    det: dict = {"compiled": True, "compile_warnings": warnings}

    # ── (a) явні I/O тести ───────────────────────────────────────────────
    if tests:
        passed, total = _run_io_tests(binary, tests)
        ratio = passed / total if total else 0.0
        det.update(mode="io_tests", tests_passed=passed,
                   tests_total=total, ratio=round(ratio, 2))
        return (2 if ratio >= 0.5 else 1), det

    # ── (b) behavioural-compare з оригіналом ─────────────────────────────
    if orig_src is not None:
        ratio, bdet = _behavioural_compare(orig_src, model_src)
        det.update(mode="behavioural", **bdet, ratio=round(ratio, 3))
        if ratio >= 0.0:    # -1.0 = original didn't compile, skip
            return (2 if ratio >= 0.5 else 1), det

    # ── (c) fallback: smoke-test ─────────────────────────────────────────
    rc, _ = _run_with_input(binary, "", timeout=3)
    smoke_ok = rc not in _CRASH_RETURNCODES
    det.update(mode="smoke_fallback",
               smoke=f"rc={rc}",
               ratio=1.0 if smoke_ok else 0.0)
    return (2 if smoke_ok else 1), det


# ─────────────────────────────────────────────────────────────────────────────
# S — Структурна схожість
# ─────────────────────────────────────────────────────────────────────────────

def _cyclomatic(src: str) -> int:
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w",
                                     delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        a = lizard.analyze_file(path)
        return sum(fn.cyclomatic_complexity for fn in a.function_list)
    finally:
        os.unlink(path)


def _ast_node_type_sequence(src: str) -> list[str]:
    """Послідовність типів AST-вузлів (без листових токенів)."""
    tree = _parse(src)
    seq = []
    def walk(n):
        if n.child_count > 0:   # тільки нелистові вузли
            seq.append(n.type)
        for c in n.children:
            walk(c)
    walk(tree.root_node)
    return seq


def _bigram_jaccard(seq1: list[str], seq2: list[str]) -> float:
    """Jaccard схожість по біграмах — краще ловить структуру ніж unigrams."""
    def bigrams(s):
        return set(zip(s, s[1:]))
    b1, b2 = bigrams(seq1), bigrams(seq2)
    if not b1 and not b2:
        return 1.0
    intersection = len(b1 & b2)
    union = len(b1 | b2)
    return intersection / union if union else 0.0


def score_S(orig: str, model_src: str) -> tuple[int, dict]:
    """
    Порівнює модель з оригінальним джерелом за трьома осями:
      1. CC ratio  (cyclomatic complexity)
      2. Function count ratio
      3. AST bigram Jaccard similarity

    combined = середнє трьох відстаней (кожна в [0,1], менше = краще)
    combined ≤ 0.25 → 2,  ≤ 0.55 → 1,  > 0.55 → 0
    """
    cc_orig  = _cyclomatic(orig)
    cc_model = _cyclomatic(model_src)
    cc_diff  = abs(cc_orig - cc_model) / max(cc_orig, 1)

    fn_orig  = count_functions_ast(orig)
    fn_model = count_functions_ast(model_src)
    fn_diff  = abs(fn_orig - fn_model) / max(fn_orig, 1)

    seq_orig  = _ast_node_type_sequence(orig)
    seq_model = _ast_node_type_sequence(model_src)
    ast_sim   = _bigram_jaccard(seq_orig, seq_model)   # similarity [0,1]
    ast_dist  = 1.0 - ast_sim                           # distance  [0,1]

    combined = (cc_diff + fn_diff + ast_dist) / 3

    if combined <= 0.25:
        score = 2
    elif combined <= 0.55:
        score = 1
    else:
        score = 0

    return score, {
        "cc_orig": cc_orig,   "cc_model": cc_model,  "cc_diff": round(cc_diff, 3),
        "fn_orig": fn_orig,   "fn_model": fn_model,  "fn_diff": round(fn_diff, 3),
        "ast_bigram_sim": round(ast_sim, 3),
        "ast_bigram_dist": round(ast_dist, 3),
        "combined": round(combined, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# R — Читабельність
# ─────────────────────────────────────────────────────────────────────────────

# Ідентифікатори які IDA / Hex-Rays генерує автоматично
_IDA_VAR_RE = re.compile(
    r'^(v\d+|a\d+|sub_[0-9a-fA-F]+|loc_[0-9a-fA-F]+'
    r'|dword_\w+|byte_\w+|word_\w+|qword_\w+|unk_\w+|off_\w+)$'
)

# IDA-специфічні типи що не повинні лишатись у реконструйованому коді
_IDA_TYPES_RE = re.compile(
    r'\b(__int64|__int128|_OWORD|_BYTE|_WORD|_DWORD|_QWORD'
    r'|HIWORD|LOWORD|LOBYTE|HIBYTE|HIDWORD|LODWORD'
    r'|__fastcall|__cdecl|__stdcall|BYREF)\b'
)

# CFF-артефакти — ТІЛЬКИ справжні ознаки, не menu patterns
# while(1)+switch є нормальним menu-driven кодом → НЕ штрафуємо
_CFF_RE = re.compile(
    r'\b\w*state\w*\s*==\s*\d+.*\bswitch\b'    # state machine: stateVar == N ... switch
    r'|\bswitch\s*\(\s*\w*dispatch\w*\s*\)'     # switch(dispatcher)
    r'|\bswitch\s*\(\s*\w*opcode\w*\s*\)',       # switch(opcode/vm)
    re.IGNORECASE | re.DOTALL,
)

# Короткі імена які є цілком легітимними (loop vars, pointers, etc.)
_VALID_SHORT = frozenset({
    'i', 'j', 'k', 'n', 'm', 'c', 'p', 'q', 'r', 's', 't',
    'x', 'y', 'z', 'fd', 'id', 'ok', 'ch', 'in',
    'buf', 'len', 'ptr', 'ret', 'err', 'tmp', 'idx',
    'cnt', 'num', 'val', 'key', 'out', 'end', 'top',
    'cur', 'new', 'old', 'max', 'min', 'sum', 'pos',
    'row', 'col', 'off', 'sz', 'ip', 'op', 'sp', 'pc',
})


def _is_meaningful_name(name: str) -> bool:
    """
    Чи є ім'я "осмисленим" (тобто результатом ручного перейменування,
    а не залишком IDA / випадковим набором літер).

    Правила:
      - IDA-формат (v1, a2, sub_xxx…)                   → НІ
      - stdlib / language built-in (printf, NULL…)       → НІ (не зараховуємо)
      - короткі ≤3 з whitelist (i, j, buf, idx…)         → ТАК
      - довгі ≥4 з letter-entropy ≥ 1.5                  → ТАК
      - все інше (короткі не з whitelist; довгі типу 'aaaa') → НІ
    """
    if _IDA_VAR_RE.match(name):
        return False
    if name in _STDLIB_NAMES:
        return False
    if len(name) <= 3:
        return name in _VALID_SHORT
    # ≥4: вимагаємо мінімальну ентропію — відкидає 'aaaa', 'xxxx', 'abcd' тощо
    return _letter_entropy(name) >= 1.5


def score_R(model_src: str) -> tuple[int, dict]:
    """
    Оцінює читабельність за чотирма компонентами:

      1. ida_var_penalty    — частка IDA-style імен (v1, a2, sub_xxx) серед
                               користувацьких ідентифікаторів (без stdlib)
      2. ida_type_hits      — кількість IDA-типів (__int64, _OWORD…) у файлі
      3. meaningful_ratio   — частка осмислених імен (див. _is_meaningful_name)
                               серед користувацьких ідентифікаторів
      4. cff_hits           — справжні маркери control-flow flattening

    «Користувацькі» ідентифікатори = всі identifier-вузли AST мінус stdlib
    (printf/scanf/NULL/main/argc/...), бо ті завжди присутні і штучно
    піднімають meaningful_ratio незалежно від роботи моделі.

    score:
      2 — ida_var_penalty < 0.05  AND  ida_types == 0  AND
          meaningful_ratio > 0.65 AND  cff_hits == 0
      1 — ida_var_penalty < 0.30  AND  ida_types ≤ 3   AND
          meaningful_ratio > 0.40
      0 — інакше
    """
    all_idents = extract_identifiers(model_src)
    # Виключаємо stdlib з знаменника — рахуємо тільки користувацькі імена
    user_idents = [i for i in all_idents if i not in _STDLIB_NAMES]
    total_ids   = len(user_idents)

    ida_vars = [i for i in user_idents if _IDA_VAR_RE.match(i)]
    ida_var_penalty = len(ida_vars) / total_ids if total_ids else 0.0

    ida_type_hits = _IDA_TYPES_RE.findall(model_src)
    ida_types     = len(ida_type_hits)

    meaningful = [i for i in user_idents if _is_meaningful_name(i)]
    meaningful_ratio = len(meaningful) / total_ids if total_ids else 0.0

    cff_hits = len(_CFF_RE.findall(model_src))

    if (ida_var_penalty < 0.05 and ida_types == 0
            and meaningful_ratio > 0.65 and cff_hits == 0):
        score = 2
    elif (ida_var_penalty < 0.30 and ida_types <= 3
          and meaningful_ratio > 0.40):
        score = 1
    else:
        score = 0

    return score, {
        "total_identifiers":      len(all_idents),
        "user_identifiers":       total_ids,
        "stdlib_filtered":        len(all_idents) - total_ids,
        "ida_var_count":          len(ida_vars),
        "ida_var_penalty":        round(ida_var_penalty, 3),
        "ida_type_hits":          ida_types,
        "ida_type_set":           list(set(ida_type_hits))[:8],
        "meaningful_count":       len(meaningful),
        "meaningful_ratio":       round(meaningful_ratio, 3),
        "cff_hits":               cff_hits,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Grade mapping
# ─────────────────────────────────────────────────────────────────────────────

def to_grade(total: int) -> int:
    return {0: 0, 1: 1, 2: 2, 3: 3, 4: 3, 5: 4, 6: 5}[total]


# ─────────────────────────────────────────────────────────────────────────────
# Головна функція
# ─────────────────────────────────────────────────────────────────────────────

def evaluate(
    original_path: str,
    model_output_path: str,
    tests: list[dict] | None = None,
) -> EvalResult:
    """
    original_path     — оригінальний C-файл (ground truth з source_files/)
    model_output_path — вивід моделі (може містити markdown)
    tests             — список I/O тестів або None/[]
    """
    if Path(model_output_path).name.startswith("._"):
        raise ValueError(f"Skipping macOS metadata file: {model_output_path}")

    orig     = Path(original_path).read_text(encoding="utf-8", errors="replace")
    raw      = Path(model_output_path).read_text(encoding="utf-8", errors="replace")
    model_src = strip_markdown(raw)

    if tests is None:
        tests = []

    # score_F тепер отримує оригінал для behavioural-compare
    F, F_det = score_F(model_src, orig_src=orig, tests=tests)
    S, S_det = score_S(orig, model_src)
    R, R_det = score_R(model_src)

    # Якщо не компілюється — R обмежується 1 (нечитабельний нероб. код < читабельний нероб.)
    if F == 0:
        R = min(R, 1)

    total = F + S + R
    return EvalResult(
        F=F, S=S, R=R,
        total=total,
        grade=to_grade(total),
        details={"F": F_det, "S": S_det, "R": R_det},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Self-check & baseline (для валідації метрики)
# ─────────────────────────────────────────────────────────────────────────────

def self_check(original_path: str) -> EvalResult:
    """
    Sanity-test: evaluate(orig, orig) ПОВИНЕН повертати 6/6.
    Якщо ні — метрика зламана.
    """
    return evaluate(original_path, original_path)


def baseline_score(original_path: str, pseudocode_path: str) -> EvalResult:
    """
    «No-AI baseline»: оцінює сирий IDA-псевдокод, ніби це вивід моделі.
    Дає поверх для абсолютних чисел («наскільки взагалі модель щось покращила»).
    """
    return evaluate(original_path, pseudocode_path)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, sys
    if len(sys.argv) < 3:
        print("Usage: python deobf_evaluator.py <original.c> <model_output.c> [tests.json]")
        sys.exit(1)
    tests = []
    if len(sys.argv) >= 4:
        tests = json.loads(Path(sys.argv[3]).read_text())
    res = evaluate(sys.argv[1], sys.argv[2], tests)
    print(json.dumps(asdict(res), indent=2, ensure_ascii=False))
