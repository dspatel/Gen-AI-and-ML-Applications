"""
app.py — TradingView CSV “Bloomberg-ish” Q&A with Gradio + (optional) FinGPT LoRA

What this app does
- Scans your TradingView export folder structure:
    BASE_EXPORT_DIR\YYYY-MM-DD\*.csv
  and auto-picks either Today’s folder or the Latest available date folder.
- Lets you select multiple CSVs (multi-file comparisons)
- Normalizes changing column headers into SQL-safe snake_case
- Adds helpful computed columns (when inputs exist)
- Builds a single in-memory DuckDB table "tv" from selected files
- Two query modes:
  1) Manual SQL (fast, deterministic): paste SQL -> runs directly
  2) LLM mode (natural language -> SQL -> DuckDB -> explanation)

Key upgrades included (per your request)
- Shows the FINAL SQL executed in the Gradio UI + prints to terminal
- Optional SQL logging to sql_log.txt
- Enforces Top-N: “top 10 …” always returns exactly 10 rows (even if LLM forgets)
- Enforces default LIMIT for safety/performance if SQL has none
- UI renders only a preview; full results are downloadable

Notes for your RTX 4090
- Forces GPU load (device_map="cuda") and fp16 for speed.
- Tokenizer is loaded in slow mode (use_fast=False) to avoid tiktoken conversion issues.
- LoRA is OFF by default (checkbox). Enabling can fail if adapter/base mismatch.
"""

import os
import re
import glob
import json
import time
from datetime import date, datetime
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
import gradio as gr

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline

# Optional LoRA support
try:
    from peft import PeftModel
    PEFT_AVAILABLE = True
except Exception:
    PEFT_AVAILABLE = False


# =========================
# CONFIG — CHANGE THESE
# =========================

BASE_EXPORT_DIR_DEFAULT = r"E:\Machine Learning\TradingView\tv_exports"

# Llama 3 base model + FinGPT LoRA (LoRA is optional/toggle)
BASE_MODEL = "meta-llama/Meta-Llama-3-8B-Instruct"
FINGPT_LORA = "FinGPT/fingpt-mt_llama3-8b_lora"

MAX_FILES_IN_DROPDOWN = 500

# UI rendering preview
DF_PREVIEW_ROWS = 200

# Hard cap on results kept in memory from DuckDB (safety)
MAX_RESULT_ROWS = 50000

# Export output directory
EXPORT_DIR = os.path.join(os.getcwd(), "exports_out")
os.makedirs(EXPORT_DIR, exist_ok=True)

# LLM pipeline cache
LLM = None
LLM_CFG = {"model_id": None, "use_lora": None}  # to reload if toggles change


# =========================
# Date-folder resolution
# =========================

def get_today_export_dir(base_dir: str, create: bool = True) -> str:
    today_str = date.today().isoformat()  # YYYY-MM-DD
    path = os.path.join(base_dir, today_str)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def get_latest_export_dir(base_dir: str) -> str:
    if not os.path.isdir(base_dir):
        raise FileNotFoundError(f"Base export directory not found: {base_dir}")

    candidates = []
    for name in os.listdir(base_dir):
        full = os.path.join(base_dir, name)
        if os.path.isdir(full):
            try:
                datetime.strptime(name, "%Y-%m-%d")
                candidates.append(name)
            except ValueError:
                pass

    if not candidates:
        return get_today_export_dir(base_dir, create=True)

    latest = max(candidates)  # works for YYYY-MM-DD
    return os.path.join(base_dir, latest)


def resolve_data_dir(base_dir: str, use_latest: bool, create_today: bool = True) -> str:
    base_dir = base_dir.strip().strip('"')
    if use_latest:
        return get_latest_export_dir(base_dir)
    return get_today_export_dir(base_dir, create=create_today)


# =========================
# File listing
# =========================

def list_csv_files(data_dir: str):
    if not data_dir or not os.path.isdir(data_dir):
        return []
    files = glob.glob(os.path.join(data_dir, "**", "*.csv"), recursive=True)
    files = sorted(files, key=os.path.getmtime, reverse=True)
    return files[:MAX_FILES_IN_DROPDOWN]


def refresh_files(base_dir: str, use_latest: bool):
    data_dir = resolve_data_dir(base_dir, use_latest, create_today=True)
    files = list_csv_files(data_dir)

    choices = []
    for f in files:
        rel = os.path.relpath(f, data_dir)
        choices.append((rel, f))  # (label, value=full path)

    return gr.Dropdown(choices=choices, value=[]), data_dir


# =========================
# Column normalization + typing
# =========================

def slugify(col: str) -> str:
    s = str(col).strip().lower()
    s = s.replace("%", " pct ")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "col"
    if re.match(r"^\d", s):
        s = "c_" + s
    return s


def normalize_columns(df: pd.DataFrame):
    mapping = {}
    used = set()
    new_cols = []

    for original in df.columns:
        base = slugify(original)
        name = base
        i = 2
        while name in used:
            name = f"{base}_{i}"
            i += 1
        used.add(name)
        new_cols.append(name)
        mapping[name] = original

    out = df.copy()
    out.columns = new_cols
    return out, mapping


def try_cast_numeric(df: pd.DataFrame):
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == "object":
            s = out[c].astype(str).str.strip()
            s2 = s.str.replace(",", "", regex=False).str.replace("%", "", regex=False)
            numeric = pd.to_numeric(s2, errors="coerce")
            if numeric.notna().mean() >= 0.60:
                out[c] = numeric
    return out


# =========================
# Computed columns layer
# =========================

def find_col(cols, *patterns):
    for p in patterns:
        rx = re.compile(p, re.I)
        for c in cols:
            if rx.fullmatch(c) or rx.search(c):
                return c
    return None


def add_computed_columns(df: pd.DataFrame):
    out = df.copy()
    cols = list(out.columns)

    price = find_col(cols, r"^price$", r".*close.*", r".*last.*")
    sma50 = find_col(cols, r"^sma_?50$", r".*sma.*50.*")
    sma100 = find_col(cols, r"^sma_?100$", r".*sma.*100.*")
    sma200 = find_col(cols, r"^sma_?200$", r".*sma.*200.*")

    high52 = find_col(cols, r".*52.*high.*", r".*52w.*high.*")
    low52 = find_col(cols, r".*52.*low.*", r".*52w.*low.*")

    mcap = find_col(cols, r"^market_capitalization$", r".*market.*cap.*", r"^market_cap.*")

    def safe_ratio(a, b):
        return np.where((b != 0) & (~pd.isna(b)) & (~pd.isna(a)), (a - b) / b, np.nan)

    if price and sma50:
        out["pct_from_sma50"] = safe_ratio(out[price].astype(float), out[sma50].astype(float))
    if price and sma100:
        out["pct_from_sma100"] = safe_ratio(out[price].astype(float), out[sma100].astype(float))
    if price and sma200:
        out["pct_from_sma200"] = safe_ratio(out[price].astype(float), out[sma200].astype(float))

    if price and sma100 and sma200:
        p = out[price].astype(float)
        s1 = out[sma100].astype(float)
        s2 = out[sma200].astype(float)
        lo = np.minimum(s1, s2)
        hi = np.maximum(s1, s2)
        out["between_sma100_sma200"] = (p >= lo) & (p <= hi)

    if price and high52:
        out["pct_from_52w_high"] = np.where(
            (out[high52].astype(float) != 0) & (~pd.isna(out[high52])),
            (out[price].astype(float) / out[high52].astype(float)) - 1.0,
            np.nan
        )
    if price and low52:
        out["pct_from_52w_low"] = np.where(
            (out[low52].astype(float) != 0) & (~pd.isna(out[low52])),
            (out[price].astype(float) / out[low52].astype(float)) - 1.0,
            np.nan
        )

    if mcap:
        mc = out[mcap].astype(float)
        out["market_cap_bucket"] = pd.cut(
            mc,
            bins=[-np.inf, 50e6, 300e6, 2e9, 10e9, 200e9, np.inf],
            labels=["micro", "small", "mid", "large", "mega", "ultra"],
        )

    return out


# =========================
# Multi-file union
# =========================

def align_and_union(dfs: list[pd.DataFrame]):
    all_cols = sorted(set().union(*[set(d.columns) for d in dfs]))
    aligned = []
    for d in dfs:
        missing = [c for c in all_cols if c not in d.columns]
        for c in missing:
            d[c] = np.nan
        aligned.append(d[all_cols])
    return pd.concat(aligned, ignore_index=True)


def parse_export_date_from_path(csv_path: str) -> str:
    parent = os.path.basename(os.path.dirname(csv_path))
    try:
        datetime.strptime(parent, "%Y-%m-%d")
        return parent
    except ValueError:
        return ""


def load_one_csv(path: str):
    df = pd.read_csv(path)

    df, mapping = normalize_columns(df)
    df = try_cast_numeric(df)

    export_date = parse_export_date_from_path(path)
    df["export_date"] = export_date

    # source_file includes date folder for uniqueness
    if export_date:
        df["source_file"] = os.path.join(export_date, os.path.basename(path))
    else:
        df["source_file"] = os.path.basename(path)

    df = add_computed_columns(df)
    return df, mapping


# =========================
# Export outputs
# =========================

def export_results(df: pd.DataFrame):
    if df is None or df.empty:
        return None, None

    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(EXPORT_DIR, f"result_{stamp}.csv")
    xlsx_path = os.path.join(EXPORT_DIR, f"result_{stamp}.xlsx")

    df.to_csv(csv_path, index=False)
    df.to_excel(xlsx_path, index=False)
    return csv_path, xlsx_path


# =========================
# Top-N / LIMIT enforcement + metric resolving
# =========================

def extract_top_n(question: str, max_n: int = 500) -> Optional[int]:
    q = (question or "").lower()
    m = re.search(r"\btop\s*(\d+)\b", q) or re.search(r"\bfirst\s*(\d+)\b", q)
    if not m:
        return None
    n = int(m.group(1))
    return max(1, min(n, max_n))


def ensure_limit(sql: str, default_limit: int = 2000) -> str:
    if not sql:
        return sql
    if re.search(r"\blimit\b", sql, flags=re.IGNORECASE):
        return sql
    return sql.rstrip().rstrip(";") + f" LIMIT {default_limit};"


def force_limit(sql: str, n: int) -> str:
    if not sql:
        return sql
    if re.search(r"\blimit\s+\d+\b", sql, flags=re.IGNORECASE):
        return re.sub(r"\blimit\s+\d+\b", f"LIMIT {n}", sql, flags=re.IGNORECASE)
    return sql.rstrip().rstrip(";") + f" LIMIT {n};"


def ensure_order_by(sql: str, col: str, desc: bool = True) -> str:
    if not sql:
        return sql
    if re.search(r"\border\s+by\b", sql, flags=re.IGNORECASE):
        return sql
    direction = "DESC" if desc else "ASC"
    return sql.rstrip().rstrip(";") + f' ORDER BY "{col}" {direction};'


def resolve_metric_column(cols: list[str], metric: str) -> Optional[str]:
    metric = (metric or "").lower().strip()
    if metric == "volume":
        candidates = ["volume_1_day", "volume", "vol"]
    elif metric in ("price", "last"):
        candidates = ["price", "last", "close"]
    else:
        candidates = []

    for c in candidates:
        if c in cols:
            return c
    return None


# =========================
# DuckDB execution
# =========================

def run_sql(df_all: pd.DataFrame, sql: str) -> pd.DataFrame:
    if re.search(r"\b(insert|update|delete|create|drop|alter)\b", sql, re.I):
        raise ValueError("Blocked potentially destructive SQL.")

    con = duckdb.connect(database=":memory:")
    con.register("tv", df_all)
    res = con.execute(sql).df()
    con.close()

    if len(res) > MAX_RESULT_ROWS:
        res = res.head(MAX_RESULT_ROWS)
    return res


# =========================
# LLM loading
# =========================

def ensure_llm(use_lora: bool):
    """
    Builds a cached text-generation pipeline.
    - Forces GPU (RTX 4090) with fp16.
    - Slow tokenizer to avoid tiktoken conversion issues.
    - Optional LoRA (FinGPT) if enabled and peft installed.
    """
    global LLM, LLM_CFG

    # Rebuild if toggles changed
    if LLM is not None and LLM_CFG["model_id"] == BASE_MODEL and LLM_CFG["use_lora"] == use_lora:
        return LLM

    HF_TOKEN = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN / HUGGINGFACE_HUB_TOKEN not found. Set env var or login via huggingface-cli.")

    print("Loading tokenizer (slow)...")
    tok = AutoTokenizer.from_pretrained(
        BASE_MODEL,
        token=HF_TOKEN,
        use_fast=False,
        legacy=True,
    )
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token

    print("Loading base model on GPU (fp16)...")
    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        token=HF_TOKEN,
        device_map="cuda",
        torch_dtype=torch.float16,
        trust_remote_code=True,
    )

    model = base

    # Optional LoRA
    if use_lora:
        if not PEFT_AVAILABLE:
            raise RuntimeError("peft not installed/available. Install peft to enable LoRA.")
        print("Applying FinGPT LoRA adapter...")
        model = PeftModel.from_pretrained(base, FINGPT_LORA)

    print("Creating pipeline...")
    LLM = pipeline(
        "text-generation",
        model=model,
        tokenizer=tok,
        do_sample=False,
        return_full_text=False,
        device=0,  # force GPU 0
    )

    # Warmup improves first-request latency
    print("Warming up model...")
    _ = LLM('Return JSON only: {"ok": true}', max_new_tokens=20)
    print("LLM ready.")

    LLM_CFG = {"model_id": BASE_MODEL, "use_lora": use_lora}
    return LLM


# =========================
# LLM -> SQL prompting
# =========================

SQL_SYSTEM = """
You convert a natural-language question into a DuckDB SQL query over a table named tv.

Return STRICT JSON with exactly:
{"sql":"...","mode":"table|scalar|explain_only","notes":"..."}

Rules:
- Use ONLY the provided safe column names.
- ALWAYS quote column names with double quotes, e.g. SELECT "symbol" FROM tv
- tv includes "source_file" and "export_date" for comparisons.
- No destructive SQL: no INSERT/UPDATE/DELETE/CREATE/DROP/ALTER.
- If user asks "top N by X", SQL MUST include: ORDER BY "X" DESC LIMIT N
- Prefer including LIMIT unless user explicitly asks for all rows.
- If question isn't answerable via SQL over tv, set mode="explain_only" and sql="".
"""


def llm_make_sql(llm, question: str, safe_cols: list[str], computed_cols: list[str], mapping_sample: dict):
    cols_text = ", ".join([f'"{c}"' for c in safe_cols])
    computed_text = ", ".join([f'"{c}"' for c in computed_cols]) if computed_cols else "(none)"

    sample_items = list(mapping_sample.items())[:60]
    mapping_text = "\n".join([f'- "{k}" <= {v}' for k, v in sample_items]) if sample_items else "(none)"

    prompt = f"""{SQL_SYSTEM}

Safe columns in tv:
{cols_text}

Computed columns available:
{computed_text}

Header mapping examples (safe <= original):
{mapping_text}

Question:
{question}

Return JSON only."""
    # SQL JSON doesn't need huge token budgets
    out = llm(prompt, max_new_tokens=200)[0]["generated_text"].strip()

    try:
        j = json.loads(out)
        if not isinstance(j, dict):
            raise ValueError("not dict")
        j.setdefault("sql", "")
        j.setdefault("mode", "explain_only")
        j.setdefault("notes", "")
        return j
    except Exception:
        return {"sql": "", "mode": "explain_only", "notes": "Model output was not valid JSON."}


def explain(llm, question: str, df_result: pd.DataFrame, sql: str, notes: str):
    preview = df_result.head(50)
    context = preview.to_csv(index=False) if len(preview) else "(no rows returned)"

    prompt = f"""You are a finance data assistant.
Answer the user's question using ONLY the query output preview below.
If you can't, say you don't know from the selected files.

SQL used:
{sql if sql else "(none)"}

Notes:
{notes if notes else "(none)"}

Query output preview (top 50 rows):
{context}

User question:
{question}

Answer concisely; cite symbols/source_file/export_date/values when possible."""
    return llm(prompt, max_new_tokens=250)[0]["generated_text"].strip()


# =========================
# Main Gradio callback
# =========================

def answer_query(base_dir: str,
                 use_latest_date_folder: bool,
                 resolved_data_dir: str,
                 selected_paths: list,
                 uploaded_files,
                 question: str,
                 use_llm: bool,
                 use_lora: bool,
                 manual_sql: str,
                 default_limit: int,
                 state):
    """
    Returns:
      (df_preview, markdown_answer, csv_file, xlsx_file, final_sql_shown, state)
    """

    question = (question or "").strip()
    manual_sql = (manual_sql or "").strip()
    default_limit = int(default_limit or 2000)

    if not question and not manual_sql:
        return None, "Enter a question or paste SQL.", None, None, "", state

    # Resolve data directory (displayed resolved_data_dir is informational; we recompute)
    data_dir = resolve_data_dir(base_dir, use_latest_date_folder, create_today=True)

    # Collect file paths from dropdown + uploads
    paths = []
    if selected_paths:
        paths.extend(selected_paths if isinstance(selected_paths, list) else [selected_paths])

    if uploaded_files:
        if isinstance(uploaded_files, list):
            paths.extend([f.name for f in uploaded_files])
        else:
            paths.append(uploaded_files.name)

    paths = list(dict.fromkeys([p for p in paths if p and os.path.exists(p)]))
    if not paths:
        return None, f"No valid CSV files selected/uploaded in: {data_dir}", None, None, "", state

    # Cache key = selected file full paths
    key = "|".join(sorted(paths))

    if state.get("key") != key:
        dfs = []
        merged_mapping = {}
        for p in paths:
            d, mapping = load_one_csv(p)
            dfs.append(d)
            merged_mapping.update(mapping)

        df_all = align_and_union(dfs)

        computed_candidates = {
            "pct_from_sma50", "pct_from_sma100", "pct_from_sma200",
            "between_sma100_sma200",
            "pct_from_52w_high", "pct_from_52w_low",
            "market_cap_bucket"
        }
        computed_cols = [c for c in df_all.columns if c in computed_candidates]

        state = {
            "key": key,
            "paths": paths,
            "df_all": df_all,
            "mapping": merged_mapping,
            "computed_cols": computed_cols,
        }

    df_all = state["df_all"]
    safe_cols = list(df_all.columns)
    computed_cols = state.get("computed_cols", [])
    mapping = state.get("mapping", {})

    # ---------------------------
    # Mode 1) Manual SQL (skip LLM)
    # ---------------------------
    if manual_sql:
        final_sql = manual_sql

        # Require querying from tv
        if not re.search(r"\bfrom\s+tv\b", final_sql, re.IGNORECASE):
            msg = 'Manual SQL must include: FROM tv  (tv is the in-memory table)'
            return None, msg, None, None, final_sql, state

        # Default LIMIT if missing
        final_sql = ensure_limit(final_sql, default_limit=default_limit)

        print("\n=== EXECUTING MANUAL SQL ===")
        print(final_sql)
        print("============================\n")

        try:
            df_result = run_sql(df_all, final_sql)
        except Exception as e:
            return None, f"SQL execution failed: {e}", None, None, final_sql, state

        ans = f"Ran your SQL against the selected files. Returned **{len(df_result)}** rows."
        csv_path, xlsx_path = export_results(df_result)
        df_to_show = df_result.head(DF_PREVIEW_ROWS)
        return df_to_show, ans, csv_path, xlsx_path, final_sql, state

    # ---------------------------
    # Mode 2) No LLM: show preview
    # ---------------------------
    if not use_llm:
        preview = df_all.head(DF_PREVIEW_ROWS)
        ans = (
            "LLM disabled. Showing a preview of the combined dataset.\n\n"
            "To run deterministic queries, paste DuckDB SQL into the 'Run SQL directly' box.\n"
            'Example:\n'
            '`SELECT "symbol","volume_1_day","source_file" FROM tv ORDER BY "volume_1_day" DESC LIMIT 10;`'
        )
        csv_path, xlsx_path = export_results(preview)
        return preview, ans, csv_path, xlsx_path, "(LLM disabled — no SQL generated)", state

    # ---------------------------
    # Mode 3) LLM -> SQL -> DuckDB
    # ---------------------------
    t0 = time.time()
    llm = ensure_llm(use_lora=use_lora)

    print("---- NEW LLM REQUEST ----")
    print("Question:", question)

    plan = llm_make_sql(llm, question, safe_cols, computed_cols, mapping)
    sql = (plan.get("sql", "") or "").strip()
    mode = plan.get("mode", "explain_only")
    notes = plan.get("notes", "")

    # If model can't answer via SQL
    if mode == "explain_only" or not sql:
        ans = plan.get("notes", "LLM could not produce SQL for this question.")
        return None, ans, None, None, "", state

    # Enforce Top-N + order-by hints + default limits (FINAL SQL is what we execute)
    final_sql = sql

    top_n = extract_top_n(question)
    if top_n is not None:
        final_sql = force_limit(final_sql, top_n)

    # Heuristic: if user asked by volume, ensure ORDER BY if missing
    if "by volume" in question.lower():
        vol_col = resolve_metric_column(safe_cols, "volume")
        if vol_col:
            final_sql = ensure_order_by(final_sql, vol_col, desc=True)

    # Always bound results if still no limit
    final_sql = ensure_limit(final_sql, default_limit=default_limit)

    print("\n=== FINAL SQL TO EXECUTE ===")
    print(final_sql)
    print("===========================\n")

    # Log final SQL
    try:
        with open("sql_log.txt", "a", encoding="utf-8") as f:
            f.write(f"\nQUESTION: {question}\nFINAL SQL:\n{final_sql}\n{'-'*60}\n")
    except Exception:
        pass

    # Execute
    try:
        df_result = run_sql(df_all, final_sql)
    except Exception as e:
        msg = f"SQL execution failed: {e}\n\nSQL:\n{final_sql}"
        return None, msg, None, None, final_sql, state

    # Explain (use preview for explanation grounding)
    ans = explain(llm, question, df_result, final_sql, notes)

    # Export full; show preview in UI
    csv_path, xlsx_path = export_results(df_result)
    df_to_show = df_result.head(DF_PREVIEW_ROWS)

    print("LLM+SQL completed in", round(time.time() - t0, 2), "sec. Rows:", len(df_result))
    return df_to_show, ans, csv_path, xlsx_path, final_sql, state


# =========================
# Gradio UI
# =========================

with gr.Blocks(title="TradingView CSV Q&A (DuckDB + LLM + Manual SQL)") as demo:
    gr.Markdown(
        "## TradingView CSV Q&A (DuckDB + LLM) — with Manual SQL + Top-N enforcement\n"
        "- Set base folder only (e.g., `E:\\Machine Learning\\TradingView\\tv_exports`)\n"
        "- App auto-uses today's date folder or latest available\n"
        "- Select multiple CSVs for comparisons\n"
        "- Paste SQL to run deterministically, or ask natural language (LLM)\n\n"
        "**Tip:** Use `source_file` and `export_date` for cross-file comparisons."
    )

    # Cache unioned dataset for current file selection
    state = gr.State({"key": None, "paths": [], "df_all": None, "mapping": {}, "computed_cols": []})

    with gr.Row():
        base_dir = gr.Textbox(label="Base export directory", value=BASE_EXPORT_DIR_DEFAULT)
        use_latest = gr.Checkbox(label="Use latest available date folder (instead of today)", value=False)

    resolved_dir = gr.Textbox(label="Resolved data directory (auto)", value="", interactive=False)

    file_multiselect = gr.Dropdown(
        label="Select one or more CSVs (from resolved date folder)",
        choices=[],
        multiselect=True,
        value=[]
    )

    upload_multi = gr.File(
        label="Or upload one or more CSVs",
        file_types=[".csv"],
        file_count="multiple"
    )

    with gr.Row():
        refresh_btn = gr.Button("Refresh file list")
        use_llm = gr.Checkbox(label="Use LLM (natural language → SQL)", value=True)
        use_lora = gr.Checkbox(label="Enable FinGPT LoRA (optional)", value=False, interactive=PEFT_AVAILABLE)
        default_limit_box = gr.Number(label="Default LIMIT (if SQL has no LIMIT)", value=2000, precision=0)
        ask = gr.Button("Ask / Run")

    question = gr.Textbox(
        label="Natural language question (LLM mode) — optional if you paste SQL below",
        lines=2,
        placeholder=(
            'Examples:\n'
            '- "top 10 symbols by volume"\n'
            '- "Compare average price change % 1 day by sector and export_date"\n'
            '- "Which source_file has the most Strong buy ratings?"\n'
        )
    )

    sql_input = gr.Textbox(
        label="Run SQL directly (skips LLM) — optional",
        lines=6,
        placeholder='Example:\nSELECT "symbol","volume_1_day","source_file" FROM tv ORDER BY "volume_1_day" DESC LIMIT 10;'
    )

    out_df = gr.Dataframe(label=f"Result Preview (top {DF_PREVIEW_ROWS} rows shown)", interactive=False)
    out_text = gr.Markdown(label="Answer / Notes")

    sql_box = gr.Textbox(label="Final SQL executed (read-only)", lines=10, interactive=False)

    with gr.Row():
        out_csv = gr.File(label="Download FULL result as CSV")
        out_xlsx = gr.File(label="Download FULL result as Excel (.xlsx)")

    # Initial load + refresh wiring
    demo.load(fn=refresh_files, inputs=[base_dir, use_latest], outputs=[file_multiselect, resolved_dir])
    refresh_btn.click(fn=refresh_files, inputs=[base_dir, use_latest], outputs=[file_multiselect, resolved_dir])
    base_dir.change(fn=refresh_files, inputs=[base_dir, use_latest], outputs=[file_multiselect, resolved_dir])
    use_latest.change(fn=refresh_files, inputs=[base_dir, use_latest], outputs=[file_multiselect, resolved_dir])

    # Ask / Run
    ask.click(
        fn=answer_query,
        inputs=[
            base_dir, use_latest, resolved_dir,
            file_multiselect, upload_multi,
            question, use_llm, use_lora,
            sql_input, default_limit_box,
            state
        ],
        outputs=[out_df, out_text, out_csv, out_xlsx, sql_box, state],
    )

# Gradio launch
if __name__ == "__main__":
    demo.launch(inbrowser=True, server_name="127.0.0.1", server_port=7860, show_error=True)
