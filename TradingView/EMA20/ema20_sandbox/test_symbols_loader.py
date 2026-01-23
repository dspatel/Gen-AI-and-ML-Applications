from __future__ import annotations

import os
from symbols_loader import load_symbols_csv, summarize_symbols


def main():
    csv_path = os.path.join(os.getcwd(), "data", "symbols.csv")

    print("\n[Module S1] Loading symbols from:")
    print(f"  {csv_path}")

    df_all = load_symbols_csv(csv_path, enabled_only=False)
    df_enabled = load_symbols_csv(csv_path, enabled_only=True)

    s_all = summarize_symbols(df_all)
    s_enabled = summarize_symbols(df_enabled)

    print("\n--- ALL SYMBOLS ---")
    print(f"Total: {s_all['total']}")
    print(f"By group: {s_all['by_group']}")
    print(f"Symbols: {s_all['symbols']}")

    print("\n--- ENABLED SYMBOLS ---")
    print(f"Total: {s_enabled['total']}")
    print(f"By group: {s_enabled['by_group']}")
    print(f"Symbols: {s_enabled['symbols']}")

    # Minimal sanity assertions
    if s_all["total"] < 1:
        raise SystemExit("ERROR: No symbols found in CSV.")
    if s_enabled["total"] < 1:
        raise SystemExit("ERROR: No ENABLED symbols found in CSV.")

    print("\n✅ Module S1 OK: symbols loaded and validated.\n")


if __name__ == "__main__":
    main()
