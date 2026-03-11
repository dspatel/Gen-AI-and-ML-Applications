param(
  [string]$Start = "2023-01-03",
  [string]$End = "2026-02-23"
)

python -m agent.main `
  --mode r6_research `
  --start $Start `
  --end $End `
  --r6-config .\r6_stable\config.research.yaml
