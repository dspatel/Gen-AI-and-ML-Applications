"""
FinGPT + TradingView CSV Q&A (Gradio) — Windows-friendly

WHAT'S NEW (your requested automation):
- You provide only BASE_EXPORT_DIR:  E:\Machine Learning\TradingView\tv_exports
- App auto-resolves the actual data directory to:
    BASE_EXPORT_DIR\YYYY-MM-DD
  (today's folder by default)
- Optional checkbox: "Use latest available date folder" instead of today's folder.

FEATURES:
1) Dynamic headers: normalize TradingView headers into SQL-safe snake_case
2) Computed columns layer: derived fields (when prerequisites exist)
3) Multi-file comparisons: query across multiple CSVs, includes source_file + export_date
4) Export: download results as CSV + Excel

IMPORTANT:
- BASE_MODEL must match the FinGPT LoRA adapter. The example uses Llama-3 8B Instruct.
- bitsandbytes is NOT required in this version (Windows reliability). We load without 4-bit.
  If you want 4-bit later, we can add it (but Windows native often breaks with bnb).
"""

import os
import re
import glob
import json
from datetime import date, datetime

import duckdb
import pandas as pd
import numpy as np
import gradio as gr

from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from transformers import LlamaTokenizer

from peft import PeftModel

import os

#HF_TOKEN = os.getenv("HF_TOKEN")
HF_TOKEN = "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"


# =========================
# CONFIG — CHANGE THESE
# =========================

# You only set THIS base directory. App will append today's date automatically.
BASE_EXPORT_DIR_DEFAULT = r"E:\Machine Learning\TradingView\tv_exports"

# Cap UI file list
MAX_FILES_IN_DROPDOWN = 500

# FinGPT LoRA adapter + base model
FINGPT_LORA = "FinGPT/fingpt-mt_llama3-8b_lora"
#BASE_MODEL = "meta-llama/Meta-Llama-3-8B-Instruct"  # ensure you have access
BASE_MODEL = "Qwen/Qwen2.5-3B-Instruct"
# Output limits
MAX_RESULT_ROWS = 50000
DF_PREVIEW_ROWS = 300

# Export output directory (where downloads are written on the machine running Gradio)
EXPORT_DIR = os.path.join(os.getcwd(), "exports_out")
os.makedirs(EXPORT_DIR, exist_ok=True)

# Cached LLM pipeline
LLM = None


# =========================
# Date-folder resolution (your requested automation)
# =========================

def get_today_export_dir(base_dir: str, create: bool = True) -> str:
    """
    Returns: base_dir\\YYYY-MM-DD (today)
    Creates the folder if create=True.
    """
    today_str = date.today().isoformat()  # YYYY-MM-DD
    path = os.path.join(base_dir, today_str)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def get_latest_export_dir(base_dir: str) -> str:
    """
    Returns the most recent YYYY-MM-DD subfolder under base_dir.
    Useful when you run the app after midnight but want yesterday's exports.
    """
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
        # If no date folders exist yet, fall back to today's path (and create it)
        return get_today_export_dir(base_dir, create=True)

    latest = max(candidates)  # lexicographically works for YYYY-MM-DD
    return os.path.join(base_dir, latest)


def resolve_data_dir(base_dir: str, use_latest: bool, create_today: bool = True) -> str:
    """
    Decides which folder to use:
      - if use_latest=True: latest date folder under base_dir
      - else: today's folder (optionally created)
    """
    base_dir = base_dir.strip().strip('"')
    if use_latest:
        return get_latest_export_dir(base_dir)
    return get_today_export_dir(base_dir, create=create_today)


# =========================
# File listing
# =========================

def list_csv_files(data_dir: str):
    """
    Find CSV files under data_dir.
    Typically data_dir is already base_dir\\YYYY-MM-DD, so recursion is optional;
    we keep it recursive in case you add nested folders later.
    """
    if not data_dir or not os.path.isdir(data_dir):
        return []
    files = glob.glob(os.path.join(data_dir, "**", "*.csv"), recursive=True)
    files = sorted(files, key=os.path.getmtime, reverse=True)
    return files[:MAX_FILES_IN_DROPDOWN]


def refresh_files(base_dir: str, use_latest: bool):
    """
    Gradio callback:
      - Resolve actual data_dir based on base_dir + date logic
      - List CSVs in that folder
      - Populate dropdown with nice labels relative to data_dir
    """
    data_dir = resolve_data_dir(base_dir, use_latest, create_today=True)
    files = list_csv_files(data_dir)

    choices = []
    for f in files:
        rel = os.path.relpath(f, data_dir)  # show relative label within the date folder
        choices.append((rel, f))            # (label, value=full path)

    # We also return the resolved data_dir text so user sees it
    return gr.Dropdown(choices=choices, value=[]), data_dir


# =========================
# Column normalization + typing
# =========================

def slugify(col: str) -> str:
    """
    Convert arbitrary header into SQL-safe snake_case identifier.
    """
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
    """
    Normalize headers -> unique safe names. Return df_norm, mapping safe->original.
    """
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
    """
    Best-effort numeric conversion for TradingView exports:
    - remove commas and %
    - cast if >=60% values are numeric
    """
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
    """
    Find first column matching any regex pattern.
    Helps handle schema variations.
    """
    for p in patterns:
        rx = re.compile(p, re.I)
        for c in cols:
            if rx.fullmatch(c) or rx.search(c):
                return c
    return None


def add_computed_columns(df: pd.DataFrame):
    """
    Adds derived columns only if prerequisites exist:
      - pct_from_sma50/100/200
      - between_sma100_sma200
      - pct_from_52w_high/low
      - market_cap_bucket
    """
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
    """
    Union across mismatched schemas by creating the union of columns.
    """
    all_cols = sorted(set().union(*[set(d.columns) for d in dfs]))
    aligned = []
    for d in dfs:
        missing = [c for c in all_cols if c not in d.columns]
        for c in missing:
            d[c] = np.nan
        aligned.append(d[all_cols])
    return pd.concat(aligned, ignore_index=True)


def parse_export_date_from_path(csv_path: str) -> str:
    """
    Extract YYYY-MM-DD folder name from:
      ...\\tv_exports\\YYYY-MM-DD\\file.csv
    Returns "" if not found.
    """
    parent = os.path.basename(os.path.dirname(csv_path))
    try:
        datetime.strptime(parent, "%Y-%m-%d")
        return parent
    except ValueError:
        return ""


def load_one_csv(path: str):
    """
    Load and normalize one CSV:
      - read
      - normalize headers
      - numeric cast
      - add source_file (relative filename)
      - add export_date (from folder name)
      - add computed columns
    """
    df = pd.read_csv(path)

    df, mapping = normalize_columns(df)
    df = try_cast_numeric(df)

    # Identify source and date for multi-file comparisons
    export_date = parse_export_date_from_path(path)
    df["export_date"] = export_date

    # Make source_file include date folder for uniqueness and easier comparisons
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
    """
    Save result dataframe to CSV and XLSX for download.
    """
    if df is None or df.empty:
        return None, None

    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(EXPORT_DIR, f"result_{stamp}.csv")
    xlsx_path = os.path.join(EXPORT_DIR, f"result_{stamp}.xlsx")

    df.to_csv(csv_path, index=False)
    df.to_excel(xlsx_path, index=False)

    return csv_path, xlsx_path


# =========================
# LLM loading (FinGPT adapter)
# =========================
"""
    Load and cache the LLM pipeline (no bitsandbytes by default for Windows reliability).
    If you have a GPU, transformers will still use it via device_map="auto" where possible.
    """
def ensure_llm():
    global LLM
    if LLM is not None:
        return LLM

    import os
    HF_TOKEN = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN not found. Set HF_TOKEN env var or login via huggingface-cli.")
    print("Loading tokenizer...")
    tok = AutoTokenizer.from_pretrained(
        BASE_MODEL,
        token=HF_TOKEN,
        use_fast=False,
        legacy=True,
    )
    print("Loading base model (this can take time)...")
    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        device_map="auto",
        trust_remote_code=True,
        token=HF_TOKEN,
    )

    # Start base-only (recommended). Re-enable LoRA later after everything loads.
    model = base
    #model = PeftModel.from_pretrained(base, FINGPT_LORA)
    print("Creating pipeline...")
    LLM = pipeline(
        "text-generation",
        model=model,
        tokenizer=tok,
        max_new_tokens=450,
        do_sample=False,
        temperature=0.0,
        return_full_text=False,
    )
    print("LLM ready.")
    return LLM


# =========================
# LLM -> SQL prompt
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
- Prefer LIMIT 200 unless user asks for more.
- If question isn't answerable via SQL over tv, set mode="explain_only" and sql="".
"""


def llm_make_sql(llm, question: str, safe_cols: list[str], computed_cols: list[str], mapping_sample: dict):
    """
    Ask LLM to output JSON with SQL.
    Provide:
      - safe_cols: columns available across all selected files
      - computed_cols: derived columns
      - mapping_sample: sample safe->original header pairs for better alignment
    """
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
    out = llm(prompt)[0]["generated_text"].strip()

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


def run_sql(df_all: pd.DataFrame, sql: str) -> pd.DataFrame:
    """
    Execute DuckDB SQL against df_all registered as table tv.
    """
    if re.search(r"\b(insert|update|delete|create|drop|alter)\b", sql, re.I):
        raise ValueError("Blocked potentially destructive SQL.")

    con = duckdb.connect(database=":memory:")
    con.register("tv", df_all)
    res = con.execute(sql).df()
    con.close()

    if len(res) > MAX_RESULT_ROWS:
        res = res.head(MAX_RESULT_ROWS)
    return res


def explain(llm, question: str, df_result: pd.DataFrame, sql: str, notes: str):
    """
    Explanation grounded only in a preview of the result dataframe.
    """
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
    return llm(prompt)[0]["generated_text"].strip()


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
                 state):
    """
    Inputs:
      - base_dir: base folder E:\\...\\tv_exports
      - use_latest_date_folder: if true pick most recent date folder
      - resolved_data_dir: displayed resolved folder, but we compute again to be safe
      - selected_paths: selected CSV paths from dropdown
      - uploaded_files: uploaded CSV objects
      - question: user's question
      - use_llm: toggle LLM usage
      - state: cached union dataframe for current selection
    Outputs:
      - df result
      - explanation
      - downloadable csv
      - downloadable xlsx
      - updated state
    """
    if not question or not question.strip():
        return None, "Enter a question.", None, None, state

    # Always re-resolve in case base_dir / checkbox changed
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

    # De-dupe + ensure existence
    paths = list(dict.fromkeys([p for p in paths if p and os.path.exists(p)]))
    if not paths:
        return None, f"No valid CSV files selected/uploaded in: {data_dir}", None, None, state

    # Cache key = set of selected basenames + maybe export_date
    key = "|".join(sorted([os.path.basename(p) for p in paths]))

    # Reload union dataframe only if file selection changed
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

    # If LLM disabled: just show combined preview
    if not use_llm:
        preview = df_all.head(DF_PREVIEW_ROWS)
        ans = (
            "LLM disabled. Showing a preview of the combined dataset.\n\n"
            "Enable LLM to ask natural-language questions, or paste DuckDB SQL like:\n"
            '`SELECT "symbol","price","source_file","export_date" FROM tv LIMIT 50`'
        )
        csv_path, xlsx_path = export_results(preview)
        return preview, ans, csv_path, xlsx_path, state

    llm = ensure_llm()

    # LLM -> SQL plan
    plan = llm_make_sql(llm, question, safe_cols, computed_cols, mapping)
    sql = plan.get("sql", "")
    mode = plan.get("mode", "explain_only")
    notes = plan.get("notes", "")

    df_result = pd.DataFrame()
    if mode in {"table", "scalar"} and sql.strip():
        try:
            df_result = run_sql(df_all, sql)
        except Exception as e:
            notes = (notes + f" | SQL execution failed: {e}").strip(" |")
            df_result = pd.DataFrame()

    # Fallback if nothing returned
    if df_result.empty:
        preview = df_all.head(DF_PREVIEW_ROWS)
        ans = explain(llm, question, preview, sql, notes)
        csv_path, xlsx_path = export_results(preview)
        return preview, ans, csv_path, xlsx_path, state

    # Explain + export final
    ans = explain(llm, question, df_result, sql, notes)
    csv_path, xlsx_path = export_results(df_result)
    return df_result, ans, csv_path, xlsx_path, state


# =========================
# Gradio UI
# =========================

with gr.Blocks(title="FinGPT + TradingView CSV Q&A (Auto Date Folder)") as demo:
    gr.Markdown(
        "## FinGPT Q&A over TradingView exports (auto date folder)\n"
        "**You set only the base folder** (e.g., `E:\\Machine Learning\\TradingView\\tv_exports`).\n"
        "The app automatically uses **today's date folder** (or the latest available folder if you toggle it).\n\n"
        "**Compare across files** using `source_file` and `export_date`."
    )

    # Cache unioned dataset for the current set of selected files
    state = gr.State({"key": None, "paths": [], "df_all": None, "mapping": {}, "computed_cols": []})

    with gr.Row():
        base_dir = gr.Textbox(label="Base export directory", value=BASE_EXPORT_DIR_DEFAULT)
        use_latest = gr.Checkbox(label="Use latest available date folder (instead of today)", value=False)

    # Show the resolved directory (auto)
    resolved_dir = gr.Textbox(label="Resolved data directory (auto)", value="", interactive=False)

    # Multi-select dropdown for files
    file_multiselect = gr.Dropdown(
        label="Select one or more CSVs (from resolved date folder)",
        choices=[],
        multiselect=True,
        value=[]
    )

    # Optional upload
    upload_multi = gr.File(
        label="Or upload one or more CSVs",
        file_types=[".csv"],
        file_count="multiple"
    )

    with gr.Row():
        refresh_btn = gr.Button("Refresh file list")
        use_llm = gr.Checkbox(label="Use FinGPT (natural language → SQL)", value=True)
        ask = gr.Button("Ask")

    question = gr.Textbox(
        label="Your question",
        lines=2,
        placeholder=(
            'Examples:\n'
            '- "Compare average price change % 1 day by sector and source_file"\n'
            '- "Which source_file has the most Strong buy ratings?"\n'
            '- "Top 20 symbols by volume where relative volume 1 day > 1.5, show export_date"\n'
        )
    )

    out_df = gr.Dataframe(label="Result DataFrame", interactive=False)
    out_text = gr.Markdown(label="Answer")

    with gr.Row():
        out_csv = gr.File(label="Download result as CSV")
        out_xlsx = gr.File(label="Download result as Excel (.xlsx)")

    # Initial load: populate dropdown + resolved_dir
    demo.load(fn=refresh_files, inputs=[base_dir, use_latest], outputs=[file_multiselect, resolved_dir])

    # Refresh button does the same
    refresh_btn.click(fn=refresh_files, inputs=[base_dir, use_latest], outputs=[file_multiselect, resolved_dir])

    # If base_dir or checkbox changes, refresh list automatically
    base_dir.change(fn=refresh_files, inputs=[base_dir, use_latest], outputs=[file_multiselect, resolved_dir])
    use_latest.change(fn=refresh_files, inputs=[base_dir, use_latest], outputs=[file_multiselect, resolved_dir])

    # Ask runs query
    ask.click(
        fn=answer_query,
        inputs=[base_dir, use_latest, resolved_dir, file_multiselect, upload_multi, question, use_llm, state],
        outputs=[out_df, out_text, out_csv, out_xlsx, state],
    )

demo.launch(server_name="127.0.0.1", server_port=7860)
