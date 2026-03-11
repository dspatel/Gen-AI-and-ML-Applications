import pandas as pd
import logging
import os
from strategy_engine import generate_signals

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_backtest(symbol, initial_capital=10000, risk_per_trade=0.02, 
                 exit_strategy='atr_rr', atr_multiplier=1.5, rr_target=2.0,
                 cooldown_bars=20, min_pullback_bars=3, min_slope_pct=0.005,
                 timeframe='1h'):
    """
    Redesigned backtester with:
    - Risk-based position sizing (Fix 5)
    - Cooldown period after exits (Fix 4)
    - Parameterized trigger timeframe and pullback settings
    
    Exit Strategies:
    - 'ema_close_below': Exits when candle closes below the EMA20.
    - 'atr_rr': ATR stop loss + fixed R:R take profit target.
    """
    logging.info(f"Backtest {symbol} | {exit_strategy} | ATR {atr_multiplier}x | RR {rr_target} | cooldown {cooldown_bars} | pullback {min_pullback_bars} | slope {min_slope_pct}")
    
    df = generate_signals(symbol, min_pullback_bars=min_pullback_bars, 
                          min_slope_pct=min_slope_pct, timeframe=timeframe)
    if df is None or df.empty:
        logging.warning("No data or signals to run simulation.")
        return pd.DataFrame()
        
    # Variables for simulation
    in_position = False
    entry_price = 0.0
    entry_time = None
    shares = 0
    capital = initial_capital
    stop_loss = 0.0
    take_profit = 0.0
    cooldown_remaining = 0  # FIX 4: Cooldown counter
    
    trades = []
    
    for i in range(1, len(df)):
        current_bar = df.iloc[i]
        prev_bar = df.iloc[i-1]
        
        # Decrement cooldown
        if cooldown_remaining > 0:
            cooldown_remaining -= 1
        
        # Check Entry
        if not in_position and prev_bar['buy_signal'] and cooldown_remaining == 0:
            entry_time = current_bar['datetime']
            entry_price = current_bar['open']
            
            if entry_price <= 0:
                continue
            
            # Calculate stop loss FIRST for position sizing
            atr_val = prev_bar.get('atr', 0)
            if atr_val == 0 or pd.isna(atr_val):
                atr_val = entry_price * 0.005  # Fallback
            
            risk_amt = atr_val * atr_multiplier
            stop_loss = entry_price - risk_amt
            take_profit = entry_price + (risk_amt * rr_target)
            
            # FIX 5: Risk-based position sizing
            # Risk a fixed % of capital, not 100%
            risk_dollars = capital * risk_per_trade
            if risk_amt > 0:
                shares = int(risk_dollars / risk_amt)
            else:
                shares = 0
                
            # Cap at what we can actually afford
            max_shares = int(capital // entry_price)
            shares = min(shares, max_shares)
            
            if shares > 0:
                cost = shares * entry_price
                capital -= cost
                in_position = True
                
        # Check Exit
        elif in_position:
            exit_triggered = False
            exit_time = None
            exit_price = 0.0
            
            if exit_strategy == 'ema_close_below':
                if current_bar['close'] < current_bar['ema_20']:
                    exit_triggered = True
                    exit_time = current_bar['datetime']
                    exit_price = current_bar['close']
                    
            elif exit_strategy == 'atr_rr':
                # Check stop loss first (conservative — assume worst case)
                if current_bar['low'] <= stop_loss:
                    exit_triggered = True
                    exit_time = current_bar['datetime']
                    exit_price = max(current_bar['open'], stop_loss) if current_bar['open'] >= stop_loss else current_bar['open']
                        
                elif current_bar['high'] >= take_profit:
                    exit_triggered = True
                    exit_time = current_bar['datetime']
                    exit_price = min(current_bar['open'], take_profit) if current_bar['open'] <= take_profit else current_bar['open']
                        
                # Failsafe: EMA breakdown while underwater
                elif current_bar['close'] < current_bar['ema_20'] and current_bar['close'] < entry_price:
                    exit_triggered = True
                    exit_time = current_bar['datetime']
                    exit_price = current_bar['close']
            
            if exit_triggered:
                revenue = shares * exit_price
                capital += revenue
                pnl = revenue - (shares * entry_price)
                pnl_pct = pnl / (shares * entry_price) if (shares * entry_price) > 0 else 0
                
                trades.append({
                    'symbol': symbol,
                    'entry_time': entry_time,
                    'entry_price': entry_price,
                    'exit_time': exit_time,
                    'exit_price': exit_price,
                    'shares': shares,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'capital_after': capital
                })
                
                in_position = False
                shares = 0
                stop_loss = 0.0
                take_profit = 0.0
                cooldown_remaining = cooldown_bars  # FIX 4: Start cooldown
                
    # Close any open positions at the end of the dataset
    if in_position:
        last_bar = df.iloc[-1]
        exit_time = last_bar['datetime']
        exit_price = last_bar['close']
        revenue = shares * exit_price
        capital += revenue
        pnl = revenue - (shares * entry_price)
        pnl_pct = pnl / (shares * entry_price) if (shares * entry_price) > 0 else 0
        
        trades.append({
            'symbol': symbol,
            'entry_time': entry_time,
            'entry_price': entry_price,
            'exit_time': exit_time,
            'exit_price': exit_price,
            'shares': shares,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'capital_after': capital,
            'note': 'End of Data Exit'
        })
        
    trades_df = pd.DataFrame(trades)
    
    if trades_df.empty:
        logging.info(f"No completed trades for {symbol}.")
    else:
        logging.info(f"Completed {len(trades_df)} trades for {symbol}.")
        
    return trades_df

if __name__ == "__main__":
    test_symbol = "SPY"
    trades = run_backtest(test_symbol, timeframe='1h')
    if not trades.empty:
        print(f"\nAll trades for {test_symbol}:")
        print(trades.to_string())
        print(f"\nFinal Capital: ${trades['capital_after'].iloc[-1]:.2f}")
        print(f"Total Trades: {len(trades)}")
    else:
        print("No trades generated.")
