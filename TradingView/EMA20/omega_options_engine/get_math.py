import re

with open("report8_portfolio_vrp.txt", "r") as f:
    text = f.read()

total_trades = sum([int(x) for x in re.findall(r"Total Simulated Trades:\s+(\d+)", text)])
print(f"Total Scaled Portfolio Trades: {total_trades}")

# Find all win rates to get an average
win_rates = [float(x) for x in re.findall(r"Win Rate:\s+([0-9.]+)", text)]
if win_rates:
    avg_win_rate = sum(win_rates) / len(win_rates)
    print(f"Average Win Rate Across Universe: {avg_win_rate:.2f}%")

# Find average winning trade size
avg_win_sizes = [float(x) for x in re.findall(r"Average Winning Trade:\s+\+([0-9.]+)", text)]
if avg_win_sizes:
    print(f"Average Winner Magnitude: +{sum(avg_win_sizes)/len(avg_win_sizes):.2f}%")
    
# Find average losing trade size
avg_loss_sizes = [float(x) for x in re.findall(r"Average Losing Trade:\s+-([0-9.]+)", text)]
if avg_loss_sizes:
    print(f"Average Loser Magnitude: -{sum(avg_loss_sizes)/len(avg_loss_sizes):.2f}%")
    
# Find average cumulative PNL
pnls = [float(x) for x in re.findall(r"Cumulative Options PnL:\s+([0-9.-]+)", text)]
if pnls:
    print(f"Average Cumulative PnL per Ticker: {sum(pnls)/len(pnls):.2f}%")
