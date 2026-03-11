# Notifications

This project produces a **single compact alert** per breakout event (per symbol, per horizon) and sends it to Discord.

## Alert layout (Discord)

Discord uses:
- **Embed title** = the event title (symbol + direction + horizon + time)
- **Embed body** = the multi-line message

Current template: `config/notification_templates.yml` (key: `default`).

### Fields you will see

1) **Breakout candle time**
- `Breakout candle: 2026-02-09 09:45 CT (-06:00)`
- This is the *timestamp of the bar that triggered the breakout rule* (i.e., the bar where the close/confirm logic crossed the ref range).

2) **Decision line**
- `Decision: SHORT (51%) — Stretched reference range • Directional bias consistent • Weak close breakout`
- `SHORT/LONG` comes from the decision engine.
- `%` is `confidence_pct` (0–100).
- The rest are the top reasons.

3) **Market story (one-liner)**
This is the “one glance” summary combining:
- OR overlap / clustering
- inflation (range expansion factor)
- regime (expansion vs trend)
- directional bias and consistency
- final decision + confidence

If you want to change wording/icons, edit:
- `interpretation/labels.yml` (label strings + icons)
- `interpretation/label_engine.py` (how labels are assembled)

4) **Reference + overlap + inflation stats**
Shows the active ref range and overlap stats:
- `Ref: low–high (W=width)`
- `Inflation`
- `OR overlap adj` = **adjacent-day overlap (Option A)** across the lookback set
- `all-days` = overlap vs today’s ref for each included day
- `pairs` = pairwise overlap ratio across lookback ORs

5) **Intensity**
Normalized breakout “strength” metrics at the trigger bar:
- `closePen`: how far the close is beyond the boundary, normalized by ref width
- `wickPen`: how far the wick extends beyond the boundary, normalized by ref width
- `body`: candle body size / ref width
- `range`: candle range (high-low) / ref width

6) **Ladder status**
When using horizons like `[3,5,9]`, ladder tells you:
- which horizon this alert corresponds to (e.g., `this 5D`)
- which horizons were already broken earlier in the day
- which horizons are broken “now”
- which horizons broke simultaneously on the same bar

## How to change the message

Edit:
- `config/notification_templates.yml`

You can:
- reorder lines
- remove lines you don’t want
- add any payload keys that exist on the event

Tip: keep the embed title short; keep the body for details.
