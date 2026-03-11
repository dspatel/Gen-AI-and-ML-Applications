from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
DOCS = ROOT / "docs"
OUTPUT_DOCX = DOCS / "ORB_HYBRID_Short_Strategy_Production_Dossier.docx"


@dataclass
class CashRun:
    label: str
    equity_csv: Path
    trades_csv: Path
    starting_cash: float

    def summarize(self) -> dict:
        if not self.equity_csv.exists() or not self.trades_csv.exists():
            return {
                "label": self.label,
                "available": False,
            }
        eq = pd.read_csv(self.equity_csv)
        tr = pd.read_csv(self.trades_csv)
        if eq.empty:
            return {"label": self.label, "available": False}
        eq["date"] = pd.to_datetime(eq["date"], errors="coerce")
        eq = eq.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        end_equity = float(eq["equity"].iloc[-1])
        growth_pct = 100.0 * (end_equity / float(self.starting_cash) - 1.0)
        start_date = eq["date"].iloc[0].date().isoformat()
        end_date = eq["date"].iloc[-1].date().isoformat()
        days = int((eq["date"].iloc[-1] - eq["date"].iloc[0]).days)
        years = max(days / 365.25, 1e-9)
        cagr_pct = 100.0 * ((end_equity / float(self.starting_cash)) ** (1.0 / years) - 1.0)

        closed = tr[tr["status"].astype(str).str.startswith("closed")].copy()
        executed = int(len(closed))
        win_rate = 100.0 * float((closed["pnl"] > 0.0).mean()) if executed > 0 else 0.0
        total_pnl = float(closed["pnl"].sum()) if executed > 0 else 0.0
        avg_pnl = float(closed["pnl"].mean()) if executed > 0 else 0.0

        return {
            "label": self.label,
            "available": True,
            "start_date": start_date,
            "end_date": end_date,
            "starting_cash": float(self.starting_cash),
            "ending_equity": end_equity,
            "growth_pct": growth_pct,
            "years": years,
            "cagr_pct": cagr_pct,
            "executed_trades": executed,
            "win_rate_pct": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
        }


def _load_summary_csv(path: Path) -> dict:
    if not path.exists():
        return {}
    d = pd.read_csv(path)
    if d.empty:
        return {}
    row = d.iloc[0].to_dict()
    return {str(k): row[k] for k in row}


def _sum_field(path: Path, field: str) -> float | None:
    if not path.exists():
        return None
    d = pd.read_csv(path)
    if d.empty or field not in d.columns:
        return None
    return float(d[field].sum())


def _fmt(x: float | int | None, n: int = 2) -> str:
    if x is None:
        return "N/A"
    return f"{float(x):,.{n}f}"


def _fmt_pct(x: float | int | None, n: int = 2) -> str:
    if x is None:
        return "N/A"
    return f"{float(x):.{n}f}%"


def _heading(text: str) -> tuple[str, bool]:
    return (text, True)


def _normal(text: str) -> tuple[str, bool]:
    return (text, False)


def _build_content() -> list[tuple[str, bool]]:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    wf_short = _load_summary_csv(REPORTS / "exit_opt_summary_short21_locked_all.csv")
    gate_spy = _load_summary_csv(REPORTS / "short_entry_gate_summary_spy.csv")

    holdout_file = REPORTS / "short_symbol_holdout_report_conservative.csv"
    holdout_base_sum = _sum_field(holdout_file, "baseline_test_total_ret_pct")
    holdout_locked_sum = _sum_field(holdout_file, "locked_test_total_ret_pct")
    holdout_opt_sum = _sum_field(holdout_file, "opt_test_total_ret_pct")

    cash_runs = [
        CashRun(
            label="OOS Test Segment, 160,000 Start Cash",
            equity_csv=REPORTS / "short_locked_cash_trades_test_corrected_equity.csv",
            trades_csv=REPORTS / "short_locked_cash_trades_test_corrected.csv",
            starting_cash=160000.0,
        ),
        CashRun(
            label="OOS Test Segment, 10,000 Start Cash",
            equity_csv=REPORTS / "short_locked_cash_trades_test_10k_corrected_equity.csv",
            trades_csv=REPORTS / "short_locked_cash_trades_test_10k_corrected.csv",
            starting_cash=10000.0,
        ),
        CashRun(
            label="All History Context, 160,000 Start Cash",
            equity_csv=REPORTS / "short_locked_cash_trades_all_corrected_equity.csv",
            trades_csv=REPORTS / "short_locked_cash_trades_all_corrected.csv",
            starting_cash=160000.0,
        ),
    ]
    cash_summaries = [x.summarize() for x in cash_runs]

    lines: list[tuple[str, bool]] = []
    lines.append(_heading("ORB HYBRID Short Strategy Production Dossier"))
    lines.append(_normal("Document purpose: production-grade strategy specification, validation evidence, and operating runbook."))
    lines.append(_normal(f"Generated: {generated_at}"))
    lines.append(_normal(""))

    lines.append(_heading("1. Executive Summary"))
    lines.append(_normal("Primary objective: generate incremental cash growth from tactical short trades while long holdings are managed separately as hold portfolio exposure."))
    lines.append(_normal("Current production candidate: locked short exit strategy on full 16-symbol universe with shared cash pool and 20% per-trade capital allocation."))
    if wf_short:
        lines.append(
            _normal(
                "Walk-forward evidence (short, locked exit vs fixed 10-day baseline): "
                f"folds={int(wf_short.get('folds', 0))}, "
                f"win_folds_vs_baseline={_fmt_pct(wf_short.get('win_folds_vs_baseline_pct'))}, "
                f"mean_test_total_ret_edge={_fmt_pct(wf_short.get('mean_test_total_ret_edge_pct'))}, "
                f"median_test_total_ret_edge={_fmt_pct(wf_short.get('median_test_total_ret_edge_pct'))}."
            )
        )
    lines.append(_normal(""))

    lines.append(_heading("2. Scope and Universe"))
    lines.append(_normal("Universe source file: TradingView/ORB_HYBRID/universes/focus_symbols_v1.txt"))
    lines.append(_normal("Symbols: SPY, AAPL, NVDA, TSLA, MSFT, AMZN, GOOGL, META, V, ADBE, AMD, MA, VGT, QQQ, QQQM, VOO."))
    lines.append(_normal("Data source: Alpaca daily bars from local cache SQLite at TradingView/ORB_HYBRID/data/alpaca_daily_cache.sqlite."))
    lines.append(_normal("Price adjustment mode: split-adjusted daily bars."))
    lines.append(_normal(""))

    lines.append(_heading("3. Strategy Definition"))
    lines.append(_normal("3.1 Entry Architecture"))
    lines.append(_normal("- Side: short only."))
    lines.append(_normal("- Base trigger: daily EMA20 cross-down regime plus downside breakout."))
    lines.append(_normal("- Breakout window: 21 trading days."))
    lines.append(_normal("- Range mode: anchored."))
    lines.append(_normal("Anchored logic:"))
    lines.append(_normal("- On daily cross-down event, snapshot previous 21-day high and low as fixed anchors."))
    lines.append(_normal("- Keep anchors fixed until next opposite cross event resets regime and anchors."))
    lines.append(_normal("- In down regime, open short on close < anchored low."))
    lines.append(_normal("- One signal per cross regime in anchored mode."))
    lines.append(_normal(""))

    lines.append(_normal("3.2 Exit Architecture (Locked Short Exit, Best Performer)"))
    lines.append(_normal("Exit type: hybrid (fixed production preset)."))
    lines.append(_normal("Parameters:"))
    lines.append(_normal("- atr_mult = 1.5"))
    lines.append(_normal("- hard_stop_atr = 1.0"))
    lines.append(_normal("- breakeven_r = 0.0"))
    lines.append(_normal("- use_ema_flip = false"))
    lines.append(_normal("- max_hold = 15 trading days"))
    lines.append(_normal("Behavior:"))
    lines.append(_normal("- Initial stop for short is above entry: max(breakout_level, entry + hard_stop_atr * ATR14)."))
    lines.append(_normal("- Trailing stop follows favorable move: lowest_price_since_entry + atr_mult * ATR14."))
    lines.append(_normal("- Position exits when high reaches stop, or when max_hold is reached."))
    lines.append(_normal("- Breakeven protection is active from first favorable R because breakeven_r = 0.0."))
    lines.append(_normal(""))

    lines.append(_normal("3.3 Capital and Cash Simulation Rules"))
    lines.append(_normal("- Shared cash pool across all symbols."))
    lines.append(_normal("- On each new trade, allocate trade_fraction * available_cash (default 20%)."))
    lines.append(_normal("- Allocated notional is reserved until exit."))
    lines.append(_normal("- On exit, reserved notional plus PnL returns to cash pool."))
    lines.append(_normal("- No leverage model beyond reserved notional."))
    lines.append(_normal("- Current simulator uses deterministic stop fill at modeled stop/close levels."))
    lines.append(_normal(""))

    lines.append(_heading("4. Validation and What Was Tested"))
    lines.append(_normal("4.1 Exit Optimization (Walk-Forward)"))
    lines.append(_normal("Method: fold-by-fold train/test optimization on exit parameters with strict temporal ordering."))
    lines.append(_normal("Conclusion: short side improved materially vs fixed-hold exits; long side did not improve robustly."))
    lines.append(_normal(""))

    lines.append(_normal("4.2 Per-Symbol Exit Optimization Holdout"))
    lines.append(_normal("Method: per-symbol train split to choose best short exit, then evaluate on unseen test split."))
    lines.append(_normal("Observed outcome: per-symbol exit tuning overfit and underperformed globally locked exit aggregate."))
    if holdout_base_sum is not None and holdout_locked_sum is not None and holdout_opt_sum is not None:
        lines.append(
            _normal(
                "Aggregate holdout total-ret sum (16 symbols, conservative profile): "
                f"baseline={_fmt(holdout_base_sum)}, "
                f"locked={_fmt(holdout_locked_sum)}, "
                f"per_symbol_optimized={_fmt(holdout_opt_sum)}."
            )
        )
    lines.append(_normal(""))

    lines.append(_normal("4.3 Entry Gate Optimization"))
    lines.append(_normal("Method: optimize entry filters (symbol features and SPY regime features) while holding locked exit constant."))
    lines.append(_normal("Result: no gate beat no-filter baseline; optimizer selected no_filter across folds."))
    if gate_spy:
        lines.append(
            _normal(
                "Evidence: "
                f"mean_test_total_ret_edge={_fmt_pct(gate_spy.get('mean_test_total_ret_edge_pct'))}, "
                f"win_folds_vs_baseline={_fmt_pct(gate_spy.get('win_folds_vs_baseline_pct'))}."
            )
        )
    lines.append(_normal(""))

    lines.append(_heading("5. Production Candidate and Cash Results"))
    lines.append(_normal("Production candidate is full 16 symbols + locked short exit + anchored 21-day short entry + shared cash pool."))
    for s in cash_summaries:
        lines.append(_normal(f"{s.get('label', 'Run')}:"))
        if not s.get("available", False):
            lines.append(_normal("- Data not available in reports folder."))
            continue
        lines.append(_normal(f"- Date range: {s['start_date']} to {s['end_date']}"))
        lines.append(_normal(f"- Starting cash: {_fmt(s['starting_cash'])}"))
        lines.append(_normal(f"- Ending equity: {_fmt(s['ending_equity'])}"))
        lines.append(_normal(f"- Cash growth: {_fmt_pct(s['growth_pct'])}"))
        lines.append(_normal(f"- Annualized cash growth (CAGR): {_fmt_pct(s['cagr_pct'])}"))
        lines.append(_normal(f"- Executed trades: {int(s['executed_trades'])}"))
        lines.append(_normal(f"- Win rate: {_fmt_pct(s['win_rate_pct'])}"))
        lines.append(_normal(f"- Total PnL: {_fmt(s['total_pnl'])}"))
        lines.append(_normal(f"- Average PnL per trade: {_fmt(s['avg_pnl'])}"))
    lines.append(_normal(""))

    lines.append(_heading("6. Why This Strategy Is Current Best"))
    lines.append(_normal("- Exit logic is robust in walk-forward comparison for short side."))
    lines.append(_normal("- Per-symbol exit optimization decreased robustness and aggregate returns."))
    lines.append(_normal("- Entry gate filtering did not add statistically robust lift in current test framework."))
    lines.append(_normal("- Full universe gives better trade activity; filtered universes improve consistency but can reduce participation and produce many zero-trade windows."))
    lines.append(_normal(""))

    lines.append(_heading("7. Risk Model and Operational Controls"))
    lines.append(_normal("- Capital at risk per trade is capped by trade_fraction and stop structure."))
    lines.append(_normal("- Maximum hold (15 bars) prevents indefinite capital lock."))
    lines.append(_normal("- Use shared cash to prevent hidden over-allocation across symbols."))
    lines.append(_normal("- Monitor open position count and reserved notional daily."))
    lines.append(_normal("- Keep separate report for skipped trades due to minimum notional constraints."))
    lines.append(_normal("- Add explicit slippage + commission before live deployment for conservative expectation setting."))
    lines.append(_normal(""))

    lines.append(_heading("8. How to Run in Production Simulation"))
    lines.append(_normal("Primary command (out-of-sample segment):"))
    lines.append(
        _normal(
            "python TradingView/ORB_HYBRID/backtest_short_locked_cash.py "
            "--symbols-file TradingView/ORB_HYBRID/universes/focus_symbols_v1.txt "
            "--db-path TradingView/ORB_HYBRID/data/alpaca_daily_cache.sqlite "
            "--breakout-window 21 --range-mode anchored --setup-max-days 15 "
            "--eval-segment test --train-ratio 0.7 "
            "--starting-cash 160000 --trade-fraction 0.2 --min-trade-notional 100 "
            "--save-trades-csv TradingView/ORB_HYBRID/reports/short_locked_cash_trades_test_corrected.csv"
        )
    )
    lines.append(_normal("All-history context run: replace --eval-segment test with --eval-segment all."))
    lines.append(_normal(""))

    lines.append(_heading("9. Monitoring Checklist"))
    lines.append(_normal("- Daily: new entries, exits, reserved notional, available cash, realized PnL."))
    lines.append(_normal("- Weekly: hit rate, average PnL, zero-trade days, concentration by symbol."))
    lines.append(_normal("- Monthly: rolling cash growth, drawdown, comparison versus fixed baseline strategy."))
    lines.append(_normal("- Quarterly: revalidate locked preset with walk-forward and holdout before any parameter changes."))
    lines.append(_normal(""))

    lines.append(_heading("10. Known Limitations"))
    lines.append(_normal("- Current simulator does not model microstructure slippage at stop execution granularity."))
    lines.append(_normal("- Open-position mark-to-market is not used in current daily cash equity snapshots."))
    lines.append(_normal("- Signal frequency can be sparse for some symbols/time windows."))
    lines.append(_normal("- Breakeven-first behavior (breakeven_r=0.0) may understate practical downside in live execution."))
    lines.append(_normal(""))

    lines.append(_heading("11. Change Log of Critical Decisions"))
    lines.append(_normal("- Moved from generic multi-side optimization to short-first objective because long alpha was weak versus hold exposure."))
    lines.append(_normal("- Chose anchored breakout range tied to daily EMA20 cross regime."))
    lines.append(_normal("- Locked exit selected after walk-forward robustness checks."))
    lines.append(_normal("- Rejected per-symbol exit optimization and entry gates due to lack of robust out-of-sample lift."))
    lines.append(_normal("- Corrected short hard-stop initialization to enforce stop above entry for short positions."))
    lines.append(_normal(""))

    lines.append(_heading("12. Source Files and Reports"))
    lines.append(_normal("Core strategy/backtest scripts:"))
    lines.append(_normal("- TradingView/ORB_HYBRID/strategy_three_layer_daily_breakout.py"))
    lines.append(_normal("- TradingView/ORB_HYBRID/exit_optimizer_walkforward.py"))
    lines.append(_normal("- TradingView/ORB_HYBRID/short_symbol_holdout_report.py"))
    lines.append(_normal("- TradingView/ORB_HYBRID/short_entry_gate_walkforward.py"))
    lines.append(_normal("- TradingView/ORB_HYBRID/backtest_short_locked_cash.py"))
    lines.append(_normal("Primary reports:"))
    lines.append(_normal("- TradingView/ORB_HYBRID/reports/exit_opt_summary_short21_locked_all.csv"))
    lines.append(_normal("- TradingView/ORB_HYBRID/reports/short_symbol_holdout_report_conservative.csv"))
    lines.append(_normal("- TradingView/ORB_HYBRID/reports/short_entry_gate_summary_spy.csv"))
    lines.append(_normal("- TradingView/ORB_HYBRID/reports/short_locked_cash_trades_test_corrected.csv"))
    lines.append(_normal("- TradingView/ORB_HYBRID/reports/short_locked_cash_trades_test_corrected_equity.csv"))
    lines.append(_normal(""))

    lines.append(_heading("13. Production Readiness Recommendation"))
    lines.append(_normal("Status: ready for paper-trading style production simulation with locked short exit on full 16-symbol universe."))
    lines.append(_normal("Before live capital: add conservative slippage/commission assumptions and run forward shadow monitoring for at least one quarter."))

    return lines


def _make_docx(lines: Iterable[tuple[str, bool]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def p_xml(text: str, bold: bool = False) -> str:
        t = escape(text)
        if bold:
            return (
                "<w:p><w:r><w:rPr><w:b/></w:rPr>"
                f"<w:t xml:space=\"preserve\">{t}</w:t>"
                "</w:r></w:p>"
            )
        if text == "":
            return "<w:p/>"
        return f"<w:p><w:r><w:t xml:space=\"preserve\">{t}</w:t></w:r></w:p>"

    body = "\n".join(p_xml(t, b) for t, b in lines)
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
 xmlns:v="urn:schemas-microsoft-com:vml"
 xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:w10="urn:schemas-microsoft-com:office:word"
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
 xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
 xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk"
 xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml"
 xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
 mc:Ignorable="w14 wp14">
  <w:body>
    {body}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>
      <w:cols w:space="708"/>
      <w:docGrid w:linePitch="360"/>
    </w:sectPr>
  </w:body>
</w:document>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""
    core = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>ORB HYBRID Short Strategy Production Dossier</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
</cp:coreProperties>
"""
    app = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Microsoft Office Word</Application>
</Properties>
"""

    with ZipFile(out_path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)
        zf.writestr("word/document.xml", document_xml)


def main() -> None:
    lines = _build_content()
    _make_docx(lines, OUTPUT_DOCX)
    print(f"Generated: {OUTPUT_DOCX}")


if __name__ == "__main__":
    main()
