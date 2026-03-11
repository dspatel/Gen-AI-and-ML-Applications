# The Meta-Learner Roadmap: Building the "Greatest Brain"

Right now, your Alpha Engine is executing pure mathematical rules. Our goal is to augment that math with pure intelligence. Data collection has officially started today.

This roadmap outlines the exact components we need to build to create an **Autonomous AI Orchestrator**. This system will not be an opinionated, hardcoded neural network. It will be an automated machine learning factory that builds, tests, and deploys *its own* neural networks based strictly on what actually makes money in the market.

## Phase 1: The "Critic" (Supervised Learning)
Before we build the Orchestrator, we build its first brain. The Critic sits between the Alpha Engine and the Execution Bridge.
*   **The Mission:** It looks at the `.json` output from the Alpha Engine and assigns a probability score to every buy signal: *What is the probability this trade is profitable after 30 days?*
*   **The Model:** We start with an incredibly robust Gradient Boosting tree (XGBoost or LightGBM). We give it the `live_trades.db` dataset containing the `state_context` (EMA values, RSI, VIX). It figures out exactly what situations the Alpha Engine usually fails in, and it vetoes those signals.

*   **Critical Engineering Nuances for Phase 1 Training:**
    *   **Disable Hardcoded Math Filters:** The training backtest must be run with the `<50 SMA` drop filter *disabled*. If the engine never logs trades below the 50 SMA, the AI will never learn why it's a structural failure. It must witness the losses.
    *   **Defeat Survivorship Bias:** We cannot train the AI using `universe_scraper.py` (which only pulls *today's* S&P 500 winners). The historical training data must use a point-in-time dataset that includes delisted and bankrupt companies, so the AI learns to identify terminal decay.

## Phase 2: The Continuous MLOps Pipeline (The Orchestrator)
This is where the magic happens. The system stops being a static program and becomes a living organism.

1.  **Automated Retraining Epochs:** Every weekend (when markets are closed), the Orchestrator wakes up. It pulls the latest week of trades from `live_trades.db`. It automatically retrains the Critic model on the fresh data, accommodating for subtle regime shifts before they become massive drawdowns.
2.  **The Shadow Evaluator:** When a new Critic model is built over the weekend, the Orchestrator runs an automated backtest across the last 90 days. If the *new* model proves to have a higher win rate and lower drawdown than the *live* model, the Orchestrator automatically hot-swaps the `.pkl` weight files. The system upgraded itself while you were asleep.
3.  **Automated Feature Engineering (LLM Guided):** If the Critic's accuracy plateaus, the Orchestrator uses an API call to a massive reasoning engine (like myself, via API). It passes its performance metrics and current data context, asking: *"Write 5 new Pandas functions to extract mathematical concepts I haven't tracked yet."* The Orchestrator safely executes that code in a sandbox, generates new data columns, and retrains the AI. If adding a new feature (like MACD divergence) improves the score, that code is merged into the live pipeline.

## Phase 3: The "AGI" Orchestrator (Open-Ended Reasoning & Self-Modification)
If we want a system that transcends quantitative limits and actually *thinks*—incorporating the latest reasoning engines available in the world (e.g., GPT-5, Claude Atlas, or specialized financial LLMs)—it cannot just write Pandas functions. It must be able to fundamentally rewrite its own internal logic and ingest the entire world State.

1.  **Multi-Modal Ingestion (The World View):** Mathematical indicators eventually exhaust themselves. The Phase 3 Orchestrator will have API access to ingest raw, unstructured reality: FOMC press conferences (audio transcripts), raw SEC 10-K filings, real-time geopolitical news streams, and satellite supply chain data. It reads the world, not just the chart.
2.  **API Routing (The Brain Swap):** The system will not have a static text model. It will operate on an Interface Architecture. We build a script that monitors open-source model repositories (like HuggingFace) or new OpenAI/Anthropic endpoints. When a superior foundational model is released to the public, the Orchestrator autonomously upgrades its API router to point its "Qualitative Analysis" module to the new, smarter brain. It stays state-of-the-art without us deploying a single patch.
3.  **Self-Modifying Architecture (The Code Evolution):** Instead of just tweaking parameters or adding data columns, the Orchestrator will have read/write access to its *own source code* in a sandboxed environment. 
    *   If it realizes that the fundamental market structure has changed (e.g., high-frequency trading has made moving average crossovers obsolete), it will use an advanced coding LLM to literally rewrite `rules_engine.py` from scratch to invent a new mathematical paradigm.
    *   It will deploy this new code to a "Shadow System" that paper trades in parallel against the current live system. 
    *   Once the new mutant code proves highly profitable over a 90-day shadow run, the Orchestrator merges its own pull request and overwrites `main`.

This is the ultimate endgame. The system stops relying on your original trading theory, and actively invents, codes, tests, and deploys its own fundamental trading philosophies.

## The Next Step: Letting the Data Age
For the next few weeks (or months, depending on your trade frequency), the absolute best thing you can do is **let the Alpha Engine run.**

We need a statistically significant sample size of both winning and losing trades in the new telemetry database. An AI cannot learn what bad trades are if we keep tweaking the engine manually to avoid them. The losses in paper trading right now are fuel for the intelligent system we build tomorrow.

When we have enough rows in that database, we spin up Phase 1.
