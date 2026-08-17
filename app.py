"""
app.py — Streamlit web UI for AI-assisted deobfuscation.

Pipeline: upload binary → IDA Pro Hex-Rays pseudocode → best LLM + prompt
          → deobfuscated C source.

Best configuration (per F+S+R study, deobf_aware run):
  • Model  : x-ai/grok-4.3  (5.57/6, 100% compile)
  • Prompt : few_shot       (5.41/6)

Run:  streamlit run app.py
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

import streamlit as st
import yaml

from ida_decompile import decompile_binary, IDAError, find_ida
from models_api import make_model
from prompts import build_messages, PROMPTS

# ─── Defaults from the F+S+R study ───────────────────────────────────────────
BEST_MODEL_ID = "x-ai/grok-4.3"
BEST_PROVIDER = "openrouter"
BEST_PROMPT = "few_shot"

CONFIG_PATH = "config.yaml"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def strip_markdown(text: str) -> str:
    """Extract C from ```c ... ``` fences; otherwise return as-is."""
    matches = re.findall(r"```(?:c|cpp)?\s*\n(.*?)```", text, re.DOTALL)
    return "\n".join(matches).strip() if matches else text.strip()


@st.cache_data(show_spinner=False)
def load_config() -> dict:
    return yaml.safe_load(open(CONFIG_PATH))


def enabled_model_options(cfg: dict) -> list[tuple[str, str]]:
    """Return (label, model_id) for every model that has a usable API key."""
    keys = cfg["api_keys"]
    opts = []
    for m in cfg["models"]:
        prov = m["provider"]
        if keys.get(prov):  # has a key
            opts.append((f"{m['id']}  ({prov})", m["id"], prov))
    return opts


# ─── Page ───────────────────────────────────────────────────────────────────

st.set_page_config(page_title="AI Deobfuscator", page_icon="🔓", layout="wide")

st.title("🔓 AI-Assisted Binary Deobfuscator")
st.caption(
    "Upload an obfuscated binary → IDA Pro Hex-Rays → LLM → clean reconstructed C. "
    "Defaults to the best model+prompt from the F+S+R study (grok-4.3 + few_shot, 5.57/6)."
)

cfg = load_config()

# ── Sidebar: configuration ────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    # Model picker
    all_models = [(m["id"], m["provider"]) for m in cfg["models"]]
    model_labels = [f"{mid}  ({prov})" for mid, prov in all_models]
    default_idx = next(
        (i for i, (mid, _) in enumerate(all_models) if mid == BEST_MODEL_ID), 0
    )
    sel = st.selectbox("Model", range(len(all_models)),
                       format_func=lambda i: model_labels[i], index=default_idx)
    model_id, provider = all_models[sel]

    prompt_name = st.selectbox(
        "Prompt strategy", list(PROMPTS.keys()),
        index=list(PROMPTS.keys()).index(BEST_PROMPT),
    )

    timeout = st.slider("LLM timeout (s)", 30, 300, 150, 10)
    ida_timeout = st.slider("IDA timeout (s)", 60, 600, 300, 30)

    # API key status
    st.divider()
    key = cfg["api_keys"].get(provider, "")
    if key:
        st.success(f"✓ API key for `{provider}` present")
    else:
        st.error(f"✗ No API key for `{provider}` in config.yaml")

    # IDA status
    try:
        ida_path = find_ida()
        st.success(f"✓ IDA found")
        st.caption(ida_path)
    except IDAError as e:
        st.error("✗ IDA Pro not found")
        st.caption(str(e)[:200])

    st.divider()
    st.markdown(
        "**Best config (study):**\n\n"
        "- Model: `x-ai/grok-4.3` — 5.57/6\n"
        "- Prompt: `few_shot` — 5.41/6\n"
        "- Deobfuscates BCF / FLA / SUB"
    )


# ── Main: file upload ──────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "Upload a binary (Mach-O / ELF / PE)",
    type=None,
    help="The binary will be decompiled with IDA Pro, then deobfuscated by the LLM.",
)

if uploaded is not None:
    # Persist upload to a temp file
    tmp_dir = tempfile.mkdtemp(prefix="deobf_app_")
    bin_path = os.path.join(tmp_dir, uploaded.name)
    with open(bin_path, "wb") as f:
        f.write(uploaded.getbuffer())

    st.info(f"Uploaded **{uploaded.name}** ({len(uploaded.getbuffer()):,} bytes)")

    run = st.button("🚀 Deobfuscate", type="primary", use_container_width=True)

    if run:
        # ── Step 1: IDA decompilation ──────────────────────────────────────
        with st.status("Running IDA Pro Hex-Rays decompilation…", expanded=True) as status:
            t0 = time.monotonic()
            try:
                pseudocode = decompile_binary(bin_path, timeout=ida_timeout)
            except IDAError as e:
                status.update(label="IDA decompilation failed", state="error")
                st.error(str(e))
                st.stop()
            ida_dt = time.monotonic() - t0
            status.update(
                label=f"IDA done in {ida_dt:.1f}s — {pseudocode.count(chr(10))} lines",
                state="complete",
            )

        with st.expander("📄 Raw IDA pseudocode", expanded=False):
            st.code(pseudocode, language="c")

        # ── Step 2: LLM deobfuscation ──────────────────────────────────────
        key = cfg["api_keys"].get(provider, "")
        if not key:
            st.error(f"No API key for provider `{provider}`. Add it to config.yaml.")
            st.stop()

        with st.status(f"Querying {model_id} with `{prompt_name}` prompt…",
                       expanded=True) as status:
            t0 = time.monotonic()
            try:
                model = make_model(
                    {"id": model_id, "provider": provider},
                    cfg["api_keys"], timeout=timeout,
                )
                messages = build_messages(prompt_name, pseudocode)
                raw, meta = model.complete(messages)
                clean = strip_markdown(raw)
            except Exception as e:
                status.update(label="LLM request failed", state="error")
                st.error(f"{type(e).__name__}: {e}")
                st.stop()
            llm_dt = time.monotonic() - t0
            toks = meta.get("total_tokens", "?")
            status.update(
                label=f"LLM done in {llm_dt:.1f}s — {toks} tokens",
                state="complete",
            )

        # ── Step 3: result ─────────────────────────────────────────────────
        st.subheader("✨ Deobfuscated C source")
        st.code(clean, language="c")

        st.download_button(
            "💾 Download .c",
            data=clean,
            file_name=Path(uploaded.name).stem + "_deobfuscated.c",
            mime="text/x-c",
            use_container_width=True,
        )

        # Metadata
        c1, c2, c3 = st.columns(3)
        c1.metric("IDA time", f"{ida_dt:.1f}s")
        c2.metric("LLM time", f"{llm_dt:.1f}s")
        c3.metric("Tokens", f"{meta.get('total_tokens', '?')}")

else:
    st.markdown(
        "👆 Upload a binary to begin.\n\n"
        "The app will:\n"
        "1. Decompile it with **IDA Pro Hex-Rays** → pseudocode\n"
        "2. Send the pseudocode to the **LLM** with the obfuscation-aware prompt\n"
        "3. Return the **deobfuscated, clean C source**"
    )
