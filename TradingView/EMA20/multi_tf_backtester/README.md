# The Institutional Alpha Engine

The Alpha Engine is a fully autonomous, production-grade algorithmic trading system designed to dynamically rotate into the strongest momentum leaders in the S&P 500 while strictly managing risk through an underlying Macro Engine. 

Over a massive 10-year blind out-of-sample backtest (2016-2026), the engine delivered a **+376.1% Total Return (+21.1% Annualized)** with a drastically reduced Max Drawdown of only **21.9%** (outperforming the S&P 500 benchmark).

---

## 🧠 System Architecture Overview

The system is fully modular and decentralized, consisting of specialized "Engines" that feed telemetry into a central "Brain":

### 1. The Rules Engine (Technical Momentum)
Scans the live market universe and assigns a raw "Alpha Score" to every stock based on blended 3-month (cyclical) and 6-month (structural) rate-of-change momentum.
*   **Multi-Timeframe Gating**: A stock is instantly disqualified unless the daily close is `> SMA 50`, `> EMA 20`, and `> SMA 200`. It only buys structural uptrends.
*   **Auto-Adaptive Parameters**: The logic is natively tied to the CBOE Volatility Index (^VIX). During quiet bull runs, the lookback lengths expand smoothly to ride long trends. During sudden market crashes or highly volatile environments, the lookbacks shrink mathematically to provide lightning-fast signal execution.
                                   
### 2. The Macro Engine (Capital Protection)
The "Risk Manager" of the portfolio. Every day, the engine checks the broader health of the S&P 500 (`SPY`).
*   **The Global Halt Signal**: If `SPY` closes below its own 200-day Simple Moving Average, the Macro Engine declares a "Bearish Regime".
*   **100% Cash Protection**: When the Global Halt is triggered, the execution bridge generates an automatic override, liquidating the entire active portfolio and allocating 100% to Cash until `SPY` reclaims its long-term trend. This physically avoids deep systemic market crashes (e.g., 2022).

### 3. The Sentiment Engine (NLP Amplification)
A real-time Natural Language Processing (NLP) filter deployed over daily financial news headlines.
*   **FinBERT Inference**: Pings local HuggingFace `ProsusAI/finbert` language models to read the news and assign strict probability scores (Positive/Negative/Neutral).
*   **The Conviction Multiplier**: If a stock is a quantitative leader *and* has surging positive sentiment (e.g., a massive earnings beat), the engine multiplies its trailing stop-loss width (e.g., from 3 ATR to 6 ATR). This allows the system to hold massive winners (like NVDA) through volatility without getting prematurely stopped out.

### 4. The Decision Engine (Portfolio Management)
The core logic that synthesizes the scores from all other engines and dictates exactly what to buy, what to sell, and how to size capital.
*   **Variable Position Sizing**: Capitalizes exclusively on pure Alpha. It scales from 0 up to a strict maximum of 10 positions natively.
*   **Equal Weight Exposure**: Allocates exactly 10% of total Account Equity per new position, ensuring that the total portfolio never exceeds 100% market exposure. If only 4 stocks legitimately pass the brutal criteria, the engine invests 40% and keeps the 60% remainder completely safe in cash.
*   **Volatility Parity Targets**: Actively generates the final `target_symbols` output list based entirely on the top percentile of the daily scores.

### 5. The Execution Bridge (Live MOC Routing)
The physical bridge translating internal paper math into real market trades via the Alpaca REST API.
*   **End of Day Trading**: The script focuses entirely on closing data (removing intraday whipsaws). It operates at 3:45 PM EST.
*   **Auto-Liquidation & Funding**: Dynamically polls real-life Alpaca account holdings. It automatically fires SELL orders for deposed leaders first, freeing up liquid capital, before firing BUY orders for new leaders.
*   **MOC Enforcement**: Routes all signals specifically as Market-On-Close orders to guarantee fills perfectly mirrored to the algorithm's end-of-day math.
*   **Market Clock Failsafe**: Actively pings the Alpaca Exchange Clock. If the calendar date is a weekend, holiday, or the market is already closed, the script safely neutralizes itself to prevent firing stale queued orders.
*   **Discord Webhook Alerts**: At the conclusion of every execution loop, the engine generates an elegant, color-coded embed detailing the total account equity, active quantitative targets, specific shares targeted, and executed liquidations. It posts this directly to your private Discord channel.
*   **Resiliency**: Wrapped in a 3-layer `retry/except` logic loop. It gracefully handles API rate limits, dropped internet connections, and HTTP 422 rejected orders, falling back mathematically to survive network downtime.

### 6. The Deep Telemetry Ledger (Meta-Learner Foundation)
For future Artificial Intelligence integration (e.g., Reinforcement Learning and advanced Critic models), the execution engine logs the exact mathematical state of the market for every trade. 
*   **Deep State Context**: Instead of just logging execution prices, `trade_telemetry.py` saves a massive JSON payload containing the stock's Exact EMAs, RSI, VIX level, Volatility, and Macro Regime to a local SQLite database (`live_trades.db`).
*   **Continuous MLOps**: This allows a future AI "Orchestrator" to learn the exact conditions when the Alpha Engine makes mistakes, automatically evolving the strategy and vetoing poor trades before they occur.

---

## 💻 Installation & Setup

1. **Clone the Repository** and navigate to the directory.
2. **Setup the Conda Environment**
   ```bash
   conda create -n alpha_engine python=3.10
   conda activate alpha_engine
   pip install -r requirements.txt
   ```
3. **Configure the Alpaca API Keys & Webhooks**
   Open `execution_engine.py` and input your personal live or paper Alpaca keys alongside your Discord Webhook URL on Lines 17-20.
   ```python
   ALPACA_API_KEY = "YOUR_LIVE_KEY"
   ALPACA_SECRET_KEY = "YOUR_LIVE_SECRET"
   DISCORD_WEBHOOK_URL = "YOUR_WEBHOOK_URL"
   ```

---

## ⚙️ How to Run Live Production

The engine is engineered as an **End-Of-Day (EOD)** absolute return system.

To deploy it locally or on an AWS server, you must trigger the execution pipeline **exactly 15 minutes before the market closes** (3:45 PM EST) on active trading days.

### The Automation Sequence (Windows Task Scheduler)

Instead of running the scripts manually, a pre-configured batch file (`run_alpha_engine.bat`) has been provided to chain the entire process together. You should automate this using the **Windows Task Scheduler**:

1. Open **Task Scheduler** from the Windows Start Menu.
2. Click **Create Basic Task...** on the right sidebar.
3. **Name**: `Alpha Engine Daily Execution`.
4. **Trigger**: Select `Daily`.
5. **Time**: Set it to **3:45:00 PM** (Make sure your system time matches EST). Recur every `1` days.
6. **Action**: Select `Start a program`.
7. **Program/Script**: Click Browse and select the `run_alpha_engine.bat` file located in your `multi_tf_backtester` folder.
8. **Start in (IMPORTANT)**: Type the absolute path to your folder: `E:\Machine Learning\TradingView\EMA20\multi_tf_backtester\`
9. **Finish & Configure Properties**: Check "Open the Properties dialog for this task when I click Finish".
10. Under **Conditions**, you can optionally check "Wake the computer to run this task" if your desktop goes to sleep.

*(Note: The internal `check_market_hours()` failsafe will automatically protect the bot from firing on weekends or NYSE holidays, even if the Task Scheduler triggers).*

### What Happens Behind the Scenes at 3:45 PM:
1. `universe_scraper.py` crawls Wikipedia and dynamically grabs the 503 currently listed tickers on the S&P 500 so you never trade a delisted stock.
2. `live_execution.py` requests the last ~200 days of price data for all 503 stocks from Yahoo Finance.
3. The Mathematical Engines run the adaptive VIX lengths, score the stocks, and invoke the Macro Bear Halt rules.
4. The system dumps the final Top 10 leaders into a `live_target_portfolio.json` file.
5. `execution_engine.py` reads the JSON file, connects to your Alpaca account, polls your live $ Equity, divides it securely by 10, calculates integer quantities, and fires the corresponding Market-On-Close orders to the exchange exactly before the bell.

---

## 🛠️ Modifying the Parameters

While the engine is fundamentally designed to "auto-adapt" via the VIX, you can structurally modify its core bounds if desired:
- **Max Portfolio Sizing**: Change `max_alloc_percent=0.10` in `execution_engine.py -> sync_portfolio()` to alter the 10% cash exposure rule. (e.g., 0.20 to concentrate in a max of 5 stocks).
- **Macro Engine Toggle**: Inside `live_execution.py`, toggle `weather = macro.get_weather()` to manually force the engine OFF or ON regardless of the SPY 200 SMA.
