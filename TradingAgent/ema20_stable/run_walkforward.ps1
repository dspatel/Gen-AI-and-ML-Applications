param(
  [string]$End = "2026-02-23",
  [string]$Symbols = "SPY",
  [string]$Config = ".\\ema20_stable\\config.walkforward.optimized.yaml"
)

python -m agent.main `
  --mode ema20_walkforward `
  --end $End `
  --symbols $Symbols `
  --ema20-config $Config
