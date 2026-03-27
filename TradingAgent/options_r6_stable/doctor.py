from __future__ import annotations

from dataclasses import asdict

from .alpaca import AlpacaCredentials, AlpacaHttpClient
from .config_loader import AppConfig, load_config
from .db import connect, init_db
from .historical_provider import build_historical_provider, list_historical_provider_capabilities
from .symbols import load_symbols


def run_doctor(config_path: str) -> dict:
    cfg: AppConfig = load_config(config_path)
    symbols = load_symbols(cfg.symbols)

    db_path = cfg.resolved_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(cfg.db.path)
    init_db(conn)
    conn.close()

    creds = AlpacaCredentials.from_env(cfg.options.paper_account_env_prefix)
    provider_manifest = {
        name: asdict(cap)
        for name, cap in list_historical_provider_capabilities().items()
    }
    out: dict = {
        "config": {
            "version": cfg.version,
            "status": cfg.status,
            "timezone": cfg.timezone,
            "db_path": str(db_path),
            "reports_dir": str(cfg.resolved_reports_dir),
            "research_output_dir": str(cfg.resolved_research_output_dir),
            "paper_account_env_prefix": cfg.options.paper_account_env_prefix,
        },
        "symbols": {
            "count": len(symbols),
            "symbols": symbols,
        },
        "options_settings": asdict(cfg.options),
        "historical_provider": {
            "configured": cfg.market_data.historical_provider,
            "available": provider_manifest,
            "active": None,
            "error": None,
        },
        "alpaca": {
            "credentials_present": bool(creds),
            "account_status": None,
            "account_id": None,
            "stock_quote_symbol": None,
            "stock_quote_ts": None,
            "stock_feed": cfg.market_data.stock_feed,
            "note": "Option entitlement and chain checks are not wired into v0.1 scaffold yet.",
        },
    }

    try:
        provider = build_historical_provider(cfg)
        out["historical_provider"]["active"] = asdict(provider.capabilities)
    except Exception as exc:
        out["historical_provider"]["error"] = str(exc)

    if creds is None:
        return out

    client = AlpacaHttpClient(credentials=creds)
    account = client.get_account()
    out["alpaca"]["account_status"] = account.get("status")
    out["alpaca"]["account_id"] = account.get("account_number")

    quote_symbol = symbols[0]
    quote = client.get_latest_stock_quote(symbol=quote_symbol, feed=cfg.market_data.stock_feed)
    quote_obj = quote.get("quote") or {}
    out["alpaca"]["stock_quote_symbol"] = quote.get("symbol") or quote_symbol
    out["alpaca"]["stock_quote_ts"] = quote_obj.get("t")
    return out
