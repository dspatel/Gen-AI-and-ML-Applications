# Opening Range and Reference Windows

## Opening Range (OR)

The opening range is defined per session using bars between:

- `08:30` inclusive and `09:00` exclusive (CT)

Computed fields:

- `or_high`
- `or_low`
- `or_width = or_high - or_low`

## Post-OR evaluation window

Entry scanning uses bars from:

- `09:00` to `14:45` (CT)

Forced time-based exit checks use:

- `14:50` (CT)

## Reselect reference windows

Reselection uses:

- `lookback_months` total prior window
- trailing `validation_months` as validation subset
- earlier portion as train subset

This ensures strategy activation uses only prior data as of reselection date.

