from __future__ import annotations

import argparse
import pickle
from pathlib import Path

import pandas as pd
try:
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "scikit-learn is required for train_trade_filter_baseline.py. "
        "Install dependencies with: pip install -r TradingView/ORB_HYBRID/requirements.txt"
    ) from exc

try:
    import joblib  # type: ignore
except Exception:
    joblib = None

DEFAULT_DATASET = Path(__file__).resolve().parent / "data" / "trade_dataset.csv"
DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "trade_filter_logreg.joblib"
DEFAULT_PRED = Path(__file__).resolve().parent / "reports" / "trade_filter_predictions.csv"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train baseline logistic trade filter on ORB trade dataset.")
    p.add_argument("--dataset-csv", default=str(DEFAULT_DATASET))
    p.add_argument("--train-ratio", type=float, default=0.7)
    p.add_argument("--threshold", type=float, default=0.55, help="Probability threshold for accepting trades.")
    p.add_argument("--save-model", default=str(DEFAULT_MODEL))
    p.add_argument("--save-predictions", default=str(DEFAULT_PRED))
    return p.parse_args()


def _top_decile_precision(y_true: pd.Series, score: pd.Series) -> float:
    if len(score) < 10:
        return float("nan")
    cutoff = score.quantile(0.9)
    mask = score >= cutoff
    if mask.sum() == 0:
        return float("nan")
    return float((y_true[mask] == 1).mean())


def main() -> None:
    args = _parse_args()
    df = pd.read_csv(args.dataset_csv)
    if df.empty:
        raise RuntimeError("Dataset is empty.")
    required = {
        "session_date",
        "symbol",
        "direction_up",
        "entry_price",
        "or_width",
        "or_width_pct",
        "confirm_rvol",
        "entry_minute_of_day",
        "entry_minutes_from_open",
        "pnl",
        "label_win",
    }
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Dataset missing columns: {missing}")

    df["session_date"] = pd.to_datetime(df["session_date"], errors="coerce")
    df = df.dropna(subset=["session_date"]).sort_values("session_date").reset_index(drop=True)
    if len(df) < 100:
        raise RuntimeError("Not enough rows for robust training. Build a larger dataset first.")

    split_idx = max(1, int(len(df) * args.train_ratio))
    split_idx = min(split_idx, len(df) - 1)
    train = df.iloc[:split_idx].copy()
    test = df.iloc[split_idx:].copy()

    num_cols = [
        "direction_up",
        "entry_price",
        "or_width",
        "or_width_pct",
        "confirm_rvol",
        "entry_minute_of_day",
        "entry_minutes_from_open",
    ]
    cat_cols = ["symbol", "exit_reason"]

    pre = ColumnTransformer(
        transformers=[
            (
                "num",
                Pipeline(
                    steps=[
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                num_cols,
            ),
            (
                "cat",
                Pipeline(
                    steps=[
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                cat_cols,
            ),
        ]
    )

    model = Pipeline(
        steps=[
            ("pre", pre),
            ("clf", LogisticRegression(max_iter=500, class_weight="balanced")),
        ]
    )

    X_train = train[num_cols + cat_cols]
    y_train = train["label_win"].astype(int)
    X_test = test[num_cols + cat_cols]
    y_test = test["label_win"].astype(int)

    model.fit(X_train, y_train)

    p_test = model.predict_proba(X_test)[:, 1]
    y_hat = (p_test >= args.threshold).astype(int)

    auc = float("nan")
    if len(set(y_test.tolist())) > 1:
        auc = float(roc_auc_score(y_test, p_test))

    acc = float(accuracy_score(y_test, y_hat))
    prec = float(precision_score(y_test, y_hat, zero_division=0))
    rec = float(recall_score(y_test, y_hat, zero_division=0))
    top10_prec = _top_decile_precision(y_test, pd.Series(p_test, index=test.index))

    pnl_all = float(test["pnl"].sum())
    accepted = test[p_test >= args.threshold].copy()
    pnl_filtered = float(accepted["pnl"].sum()) if not accepted.empty else 0.0

    print("=== Trade Filter Baseline (LogReg) ===")
    print(f"train_rows={len(train)} test_rows={len(test)}")
    print(f"train_start={train['session_date'].min().date()} train_end={train['session_date'].max().date()}")
    print(f"test_start={test['session_date'].min().date()} test_end={test['session_date'].max().date()}")
    print(f"auc={auc:.4f} accuracy={acc:.4f} precision={prec:.4f} recall={rec:.4f} top10_precision={top10_prec:.4f}")
    print(f"test_pnl_take_all={pnl_all:.2f}")
    print(f"test_pnl_filtered={pnl_filtered:.2f} accepted_trades={len(accepted)} threshold={args.threshold:.2f}")
    print(f"delta_pnl_vs_all={pnl_filtered - pnl_all:.2f}")

    if args.save_model.strip():
        out_model = Path(args.save_model)
        out_model.parent.mkdir(parents=True, exist_ok=True)
        if joblib is not None:
            joblib.dump(model, out_model)
            print(f"saved_model={out_model}")
        else:
            with out_model.open("wb") as f:
                pickle.dump(model, f)
            print(f"saved_model={out_model} (pickle fallback)")

    if args.save_predictions.strip():
        out_pred = Path(args.save_predictions)
        out_pred.parent.mkdir(parents=True, exist_ok=True)
        pred_df = test.copy()
        pred_df["p_win"] = p_test
        pred_df["accept"] = (pred_df["p_win"] >= args.threshold).astype(int)
        pred_df.to_csv(out_pred, index=False)
        print(f"saved_predictions={out_pred}")


if __name__ == "__main__":
    main()
