import re

text = open('backtest_results.txt', encoding='utf-16le').read()
blocks = text.split('BLIND DATA OPTIONS PERFORMANCE')

with open('parsed_pnl.txt', 'w') as f:
    for b in blocks[1:]:
        # Find ticker
        ticker = b.split('(')[1].split(')')[0]
        
        # Extract metrics using simple string searching
        win_rate = ""
        avg_win = ""
        avg_loss = ""
        cum_pnl = ""
        
        for line in b.split('\n'):
            if "Win Rate:" in line: win_rate = line.split("Win Rate:")[1].strip()
            if "Average Winning Trade:" in line: avg_win = line.split("Average Winning Trade:")[1].strip()
            if "Average Losing Trade:" in line: avg_loss = line.split("Average Losing Trade:")[1].strip()
            if "Cumulative Options PnL:" in line: cum_pnl = line.split("Cumulative Options PnL:")[1].strip()
            
        f.write(f"{ticker:<5} | Win: {win_rate:<7} | Avg Win: {avg_win:<7} | Avg Loss: {avg_loss:<7} | PnL: {cum_pnl}\n")
