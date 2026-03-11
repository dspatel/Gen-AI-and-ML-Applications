param(
  [string]$Start = "2023-01-03",
  [string]$End = "2026-02-23"
)

python -m agent.main `
  --mode ema20_research `
  --start $Start `
  --end $End `
  --ema20-config .\ema20_stable\config.research.yaml

