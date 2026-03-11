param(
  [string]$Start = "2023-01-03",
  [string]$End = "2026-02-23",
  [string]$Symbols = "SPY",
  [string]$Config = ".\\ema20_stable\\config.walkforward.optimized.yaml",
  [int]$TrainMonths = 18,
  [int]$ValidateMonths = 6,
  [int]$TestMonths = 3,
  [int]$StepMonths = 6,
  [double]$MinTestExcessVsBH = 0.0
)

python -m agent.main `
  --mode ema20_rolling `
  --start $Start `
  --end $End `
  --symbols $Symbols `
  --ema20-config $Config `
  --ema20-train-months $TrainMonths `
  --ema20-validate-months $ValidateMonths `
  --ema20-test-months $TestMonths `
  --ema20-step-months $StepMonths `
  --ema20-min-test-excess-vs-bh $MinTestExcessVsBH
