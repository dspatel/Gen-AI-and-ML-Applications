import sqlite3
import pandas as pd
import numpy as np
import os
import yfinance as yf

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'backtest_data.db')

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def compute_sma(series, period):
    return series.rolling(window=period).mean()

def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def compute_rsi(close, period=14):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def compute_macd(close, fast=12, slow=26, signal=9):
    ema_fast = compute_ema(close, fast)
    ema_slow = compute_ema(close, slow)
    macd = ema_fast - ema_slow
    sig = compute_ema(macd, signal)
    hist = macd - sig
    return macd, sig, hist

class PortfolioDataEngine:
    def __init__(self):
        self.conn = get_db_connection()
        self.daily = None
        
    def load_all_data(self):
        """Loads all symbols into a single MultiIndex DataFrame or dict of DataFrames"""
        print("Loading all portfolio data from DB...")
        df = pd.read_sql("SELECT symbol, date, open, high, low, close, volume FROM daily_bars ORDER BY date", self.conn)
        df['date'] = pd.to_datetime(df['date'])
        
        # Ensure ^VIX is available, even if the database scraper missed it
        if '^VIX' not in df['symbol'].values:
            print("VIX not found in DB. Fetching live VIX data...")
            vix_data = yf.download('^VIX', start='2015-01-01', progress=False)
            if not vix_data.empty:
                # Flatten multi-index columns if they exist
                if isinstance(vix_data.columns, pd.MultiIndex):
                    vix_data.columns = vix_data.columns.droplevel(1)
                vix_data = vix_data.reset_index()
                vix_data.rename(columns={'Date': 'date', 'Close': 'close', 'Open': 'open', 'High':'high', 'Low':'low', 'Volume': 'volume'}, inplace=True)
                vix_data['symbol'] = '^VIX'
                df = pd.concat([df, vix_data], ignore_index=True)
        
        # Calculate Universal Technicals for all symbols
        result = {}
        for sym, grp in df.groupby('symbol'):
            grp = grp.sort_values('date').copy()
            grp.set_index('date', inplace=True)
            
            # Key regime filters
            grp['sma_50'] = compute_sma(grp['close'], 50)
            grp['sma_200'] = compute_sma(grp['close'], 200)
            grp['ema_20'] = compute_ema(grp['close'], 20)
            
            # ----------------------------------------------------------------
            # ADAPTIVE PARAMETER ENGINE (Phase 11)
            # ----------------------------------------------------------------
            # If the market is violently volatile, shrink the lookbacks to be fast.
            # If the market is quiet, expand the lookbacks to ride slow trends.
            # Baseline VIX: 15.0 -> Multiplier ~ 1.0
            # High VIX: 30.0 -> Multiplier ~ 0.5 (shorter MAs)
            # Low VIX: 10.0 -> Multiplier ~ 1.5 (longer MAs)
            vix_df = df[df['symbol'] == '^VIX'].set_index('date').reindex(grp.index, method='ffill')
            
            if not vix_df.empty and 'close' in vix_df.columns:
                vix_close = vix_df['close'].replace(0, np.nan).fillna(15.0)  # Default baseline
                
                # Inverse scaling: VIX 15 is 1.0. VIX 30 is 0.5. VIX 10 is 1.5.
                # Bound between 0.2x (extreme crash) and 2.0x (extreme quiet)
                ma_mult = np.clip(15.0 / vix_close, 0.2, 2.0)
                
                # We can't use pandas rolling with dynamic windows easily without a loop.
                # So we approximate it by computing a fast, normal, and slow SMA, 
                # then blending them based on the volatility multiplier.
                fast_sma = compute_sma(grp['close'], 10)
                norm_sma = compute_sma(grp['close'], 50)
                slow_sma = compute_sma(grp['close'], 100)
                
                # Blend the SMAs:
                # If ma_mult < 1.0 (volatile): blend between fast and norm
                # If ma_mult >= 1.0 (quiet): blend between norm and slow
                grp['adaptive_sma'] = np.where(
                    ma_mult < 1.0,
                    fast_sma * (1 - ma_mult) + norm_sma * ma_mult,
                    norm_sma * (2 - ma_mult) + slow_sma * (ma_mult - 1)
                )
                
                # Do the same for EMA 20 (base 20: 5 fast, 40 slow)
                fast_ema = compute_ema(grp['close'], 5)
                norm_ema = compute_ema(grp['close'], 20)
                slow_ema = compute_ema(grp['close'], 40)
                
                grp['adaptive_ema'] = np.where(
                    ma_mult < 1.0,
                    fast_ema * (1 - ma_mult) + norm_ema * ma_mult,
                    norm_ema * (2 - ma_mult) + slow_ema * (ma_mult - 1)
                )
                
                grp['vix_level'] = vix_close
            else:
                grp['adaptive_sma'] = grp['sma_50']
                grp['adaptive_ema'] = grp['ema_20']
                grp['vix_level'] = 15.0
            
            # Multi-Timeframe (MTF) Filter: 10-Week SMA
            weekly_close = grp['close'].resample('W-FRI').last()
            weekly_sma_10 = weekly_close.rolling(window=10).mean()
            grp['weekly_sma_10'] = weekly_sma_10.reindex(grp.index, method='ffill')
            
            # Momentum / Oscillators
            grp['rsi_14'] = compute_rsi(grp['close'], 14)
            grp['macd'], grp['macd_sig'], grp['macd_hist'] = compute_macd(grp['close'])
            
            # Relative Strength (3-Month / 63 trading days, 6-Month / 126 days)
            grp['ret_3m'] = grp['close'].pct_change(63)
            grp['ret_6m'] = grp['close'].pct_change(126)
            
            # Volatility (ATR roughly approximated using daily variance for speed)
            grp['atr_14'] = grp['high'].rolling(14).max() - grp['low'].rolling(14).min()
            
            result[sym] = grp
            
        self.daily = result
        return result

class MacroEngine:
    """The Risk Manager. Only looks at the S&P 500 (SPY)."""
    def __init__(self, data_engine, index_sym='SPY'):
        self.data_engine = data_engine
        self.index_sym = index_sym
        
    def get_weather(self, current_date):
        """Returns True if the market is Bullish, False if Bearish"""
        spy_df = self.data_engine.daily[self.index_sym]
        if current_date not in spy_df.index:
            # Fallback to closest previous date
            past = spy_df[spy_df.index <= current_date]
            if past.empty: return False
            row = past.iloc[-1]
        else:
            row = spy_df.loc[current_date]
            
        if pd.isna(row['sma_200']): return False
        
        return row['close'] > row['sma_200']

class RulesEngine:
    """The Quantitative Analyst. Ranks the 16 symbols every day."""
    def __init__(self, data_engine):
        self.data_engine = data_engine
        
    def score_symbols(self, current_date):
        """
        Returns a Ranked DataFrame of symbols for a specific date.
        Scoring logic:
        - Must be > Daily 50 SMA (Trend Filter)
        - Must be > Daily EMA 20 (Strong short-term sentiment)
        - Ranked primarily by 3-Month Relative Strength (ret_3m)
        """
        scores = []
        for sym, df in self.data_engine.daily.items():
            if sym in ['SPY', '^VIX']: continue # Benchmarks/metrics, we don't trade them directly in the pool
                
            if current_date not in df.index:
                past = df[df.index <= current_date]
                if past.empty: continue
                row = past.iloc[-1]
            else:
                row = df.loc[current_date]
                
            if pd.isna(row['sma_50']) or pd.isna(row['ret_3m']) or pd.isna(row['sma_200']):
                continue
                
            # Score is a blend of 3-month and 6-month momentum to capture structural trends
            ret_6m_val = row['ret_6m'] if not pd.isna(row.get('ret_6m', np.nan)) else row['ret_3m']
            score = (row['ret_3m'] * 0.6) + (ret_6m_val * 0.4)
            
            scores.append({
                'symbol': sym,
                'score': score,
                'close': row['close'],
                'sma_50': row.get('adaptive_sma', row['sma_50']), # Use adaptive
                'sma_200': row['sma_200'],
                'ema_20': row.get('adaptive_ema', row['ema_20']), # Use adaptive
                'vix': row.get('vix_level', 15.0),
                'rsi': row['rsi_14'],
                'macd_hist': row['macd_hist'],
                'atr': row['atr_14']
            })
            
        scoring_df = pd.DataFrame(scores)
        if scoring_df.empty: return pd.DataFrame()
        
        # Sort descending
        scoring_df = scoring_df.sort_values(by='score', ascending=False)
        return scoring_df

if __name__ == "__main__":
    db = PortfolioDataEngine()
    db.load_all_data()
    
    macro = MacroEngine(db)
    rules = RulesEngine(db)
    
    test_date = pd.to_datetime('2024-03-01')
    print(f"--- Engines Test for {test_date.date()} ---")
    weather = macro.get_weather(test_date)
    print(f"Macro Weather (SPY > 200 SMA): {'BULLISH (Trading ON)' if weather else 'BEARISH (Trading OFF)'}")
    
    if weather:
        leaderboard = rules.score_symbols(test_date)
        print("\n--- Daily Leaderboard ---")
        print(leaderboard.head(5).to_string(index=False))
