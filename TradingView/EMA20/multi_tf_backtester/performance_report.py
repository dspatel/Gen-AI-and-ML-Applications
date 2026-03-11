import pandas as pd
import logging
from backtest_runner import run_backtest

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def generate_report(symbol):
    trades = run_backtest(symbol)
    
    if trades.empty:
        logging.info(f"--- Performance Report for {symbol} ---")
        logging.info("No trades executed.")
        return
        
    total_trades = len(trades)
    winning_trades = trades[trades['pnl'] > 0]
    losing_trades = trades[trades['pnl'] <= 0]
    
    win_rate = len(winning_trades) / total_trades * 100
    
    gross_profit = winning_trades['pnl'].sum() if not winning_trades.empty else 0
    gross_loss = abs(losing_trades['pnl'].sum()) if not losing_trades.empty else 0
    
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    avg_win = winning_trades['pnl'].mean() if not winning_trades.empty else 0
    avg_loss = losing_trades['pnl'].mean() if not losing_trades.empty else 0
    
    initial_capital = 10000 # assumed from runner default
    final_capital = trades['capital_after'].iloc[-1]
    total_return_pct = ((final_capital - initial_capital) / initial_capital) * 100
    
    # Very basic drawdown calc based on trade-to-trade capital
    cum_max = trades['capital_after'].cummax()
    drawdown = (cum_max - trades['capital_after']) / cum_max * 100
    max_drawdown = drawdown.max()
    
    print("\n" + "="*50)
    print(f" PERFORMANCE REPORT: {symbol}")
    print("="*50)
    print(f"Total Trades:      {total_trades}")
    print(f"Win Rate:          {win_rate:.2f}%")
    print(f"Profit Factor:     {profit_factor:.2f}")
    print(f"Total Return:      {total_return_pct:.2f}% (Final Cap: ${final_capital:.2f})")
    print(f"Max Drawdown:      {max_drawdown:.2f}%")
    print(f"Average Win:       ${avg_win:.2f}")
    print(f"Average Loss:      ${avg_loss:.2f}")
    print("="*50 + "\n")

if __name__ == "__main__":
    test_symbols = ["SPY", "QQQ", "AAPL"]
    for sym in test_symbols:
        generate_report(sym)
