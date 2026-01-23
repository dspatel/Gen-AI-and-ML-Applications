import os
import pandas as pd
from datetime import datetime as dt
from pathlib import Path
from tvDatafeed import TvDatafeed, Interval
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.FileHandler(f"tvdata.log", mode="w"),
        logging.StreamHandler(),
    ],
)

dataPath = r"Data"
# If no such folder exists, create an empty folder
if not os.path.exists(dataPath):
    os.mkdir(dataPath)
    logging.info(f"creating Directory {dataPath}")

def downloadData(Sym, Exchange):
    try:
        df = tv.get_hist(
            Sym, Exchange, Interval.in_5_minute, n_bars=5000, extended_session=True
        )
    except Exception:
        logging.exception("TvDataFeed Error")
        raise
    df.insert(0, "date", df.index.date)
    df.insert(1, "time", df.index.time)
    df.reset_index(inplace=True)
    del df["datetime"]
    del df["symbol"]

    return df

# get credentials for tradingview
username = "vishu723"
password = "Tradingview123$"
# initialize tradingview
tv = TvDatafeed(username=username, password=password, pro=True)

if __name__ == "__main__":
    symList = [
        ("SPY", "AMEX"),
    ]

    logging.info("Starting import...")
    try:
        for i, sym in enumerate(symList):
                logging.info(f"Processing {sym}")
                info = tv.search_symbol(sym[0], sym[1])
                try:
                    data = downloadData(sym[0], sym[1])
                except Exception:
                    continue
                filename = os.path.join(dataPath, sym[0] + ".csv")
                logging.info(f"Writing {filename}")
                data.to_csv(
                    filename,
                    columns=[
                        "date",
                        "time",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                    ],
                    index=False,
                )
                
        logging.info("Finished Import")

    except Exception:
        logging.exception()