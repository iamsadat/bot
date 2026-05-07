# TradeBot

This repo contains two things that share the same decision engine:

| Component                    | Where        | What it is                                      |
| ---------------------------- | ------------ | ----------------------------------------------- |
| Mock trading bot (CLI)       | `tradebot/`  | Headless backtester + live rich-terminal dashboard. Synthetic data, no broker. |
| Live web app (paper-first)   | `app/` + `web/` | FastAPI backend + React frontend that talks to **Alpaca**. Paper trading by default; live mode behind a confirmation phrase. |

Start with the **mock bot** to understand the strategy. Use the **web app**
to watch it run on the real market in paper mode. Only consider live
trading after you have weeks of audit-log evidence that it works on real
ticks. See [`app/README.md`](app/README.md) for the live-app docs.

---

# TradeBot mock bot — a precise day-trading sandbox

A single-symbol, intraday mock trading bot built around **multi-signal
confluence**, **strict risk management**, and a **rich live dashboard** in
the terminal. It runs entirely on synthetic market data so it is safe to
experiment with — no exchange credentials, no real orders.

It is designed to be:

* **Precise** — every entry must clear a weighted confluence score *and* a
  minimum number of agreeing indicators *and* an ADX trend-strength gate
  *and* a session-VWAP location filter, then survive an overbought/oversold
  veto.
* **Safe** — fixed-fractional position sizing using ATR stops, take-profits
  at a configurable reward:risk ratio, ATR-based trailing stops, a daily
  loss circuit breaker, and a cool-down after consecutive losers.
* **Honest** — entries fill at the *next* bar's open (no look-ahead),
  bar-internal stop/TP collisions resolve to the worst case, slippage and
  per-share commissions are charged on every fill.
* **Explainable** — every decision is recorded with its score, its
  per-signal votes, and a human-readable reason. The report includes the
  full per-bar decision log as CSV.

---

## Quick start

```bash
# 1. Install the dependencies (Python 3.10+)
pip install -r requirements.txt

# 2. Run a fast headless backtest
python -m tradebot backtest --days 20 --seed 1

# 3. Watch the bot trade live in the terminal
python -m tradebot simulate --days 5 --seed 1 --speed 60

# 4. Open the generated report
xdg-open reports/MOCK_seed1_20d/report.md   # or just `cat` it
```

The first command finishes in a couple of seconds and writes a markdown
report, an equity-curve PNG, a `trades.csv`, and a `decisions.csv` (one row
per bar with the score and indicator state) under `reports/`.

---

## How the decision engine works

On every bar the bot computes the indicator bundle below, converts each
indicator into a **vote in `[-1, +1]`**, and combines the votes into a
weighted aggregate **score in `[-1, +1]`**.

| Bucket           | Indicator         | Default weight | Vote logic |
| ---------------- | ----------------- | -------------: | --- |
| Trend            | EMA stack (9/21/55) | 0.18 | Full vote when price > EMA9 > EMA21 > EMA55 (or inverse). |
| Trend            | MACD              | 0.18 | Sign of line vs. signal, magnitude from histogram slope. |
| Trend            | ADX direction     | 0.14 | Scaled by `(ADX – adx_min) / 30`, signed by +DI vs. –DI. |
| Momentum         | RSI(14)           | 0.13 | Linear in 50 → 70 (long) and 30 → 50 (short); contrarian penalty above 70 / below 30. |
| Momentum         | Stochastic %K/%D  | 0.12 | Crossover detection plus mid-range bias. |
| Mean reversion   | VWAP deviation    | 0.08 | `tanh`-style of `(close − VWAP) / ATR`. |
| Mean reversion   | Bollinger %B      | 0.07 | Centred so 0.5 is neutral. |
| Volume confirm   | OBV vs OBV-EMA21  | 0.10 | Sign and magnitude of (OBV − OBV-EMA). |

A long entry fires only when **all** of the following hold (short is the
mirror image):

1. `score ≥ entry_threshold` (default `0.50`).
2. At least `min_agreeing_signals = 5` of the 8 votes are ≥ `+0.25`.
3. `ADX ≥ adx_min` (default `22`) — no chasing weak trends.
4. `close > VWAP` — trade with the institutional benchmark, not against it.
5. `RSI < rsi_long_max = 70` — never chase an overbought top.
6. The bar is past the session warmup (`warmup_bars = 15`) and before the
   end-of-day cool-off (`cooldown_bars = 10`).
7. We are not in a `post_trade_cooldown` window or a daily-loss halt.

If a long passes, the order is queued and **fills at the next bar's open**
with `slippage_bps = 1` and `$0.005/share` commission.

### Why the confluence floor matters

Many indicators agree directionally during chop. Without the
`min_agreeing_signals` rule, the score crosses `entry_threshold` whenever a
couple of strong signals shout — which is exactly when the others are
silent because there's no real trend. Requiring 5/8 agreement turns the
score from a sum of magnitudes into evidence that a coherent regime is in
place.

---

## Risk management

Every trade is sized so the *worst-case* loss equals
`risk_per_trade × equity` (default 0.75 %). Concretely, given the bar's
ATR(14):

```
stop_distance = stop_atr_mult * ATR              # default 1.5 × ATR
qty = floor( (risk_per_trade * equity) / stop_distance )
qty = min(qty, floor(max_position_pct * equity / price))
```

* **Stop-loss** is placed at `entry ∓ stop_distance` (sign by direction).
* **Take-profit** at `entry ± rr_ratio * stop_distance`
  (default reward:risk **2.2 : 1**). With a strict 41 %+ win rate this is
  positive expectancy.
* **Trailing stop** ratchets every bar by `trail_atr_mult × ATR` — never
  loosens, only tightens.
* **End-of-day exit**: any open position is forcibly flattened in the last
  `cooldown_bars` of the session — the bot never carries overnight risk.
* **Daily loss circuit breaker**: if cumulative day P&L hits
  `−daily_loss_limit_pct` (default 2.5 %) trading halts until the next
  session.
* **Consecutive-loss cool-down**: after `consec_loss_cooldown` losers in a
  row (default 3) the bot pauses for `cooldown_bars` (default 30) bars.

Bar-internal stop/TP collisions resolve **to the stop** — the most
pessimistic interpretation — so backtest P&L will not be flattered by a
lucky tie.

---

## CLI reference

```text
python -m tradebot --help

Usage: python -m tradebot [OPTIONS] COMMAND [ARGS]...

  TradeBot — mock day-trading bot with multi-signal confluence.

Commands:
  backtest   Run a fast headless backtest and write a markdown report.
  simulate   Stream a backtest live in the terminal with a rich dashboard.
  report     Re-render a markdown report from a pickled BacktestResult.
```

All three commands share the same option set (extracted via
`@common_options` in `tradebot/cli.py`):

| Option              | Default      | Meaning                                   |
| ------------------- | -----------: | ----------------------------------------- |
| `--symbol`          | `MOCK`       | Ticker label used in the report title.    |
| `--days`            | `20`         | Synthetic trading days to generate.       |
| `--seed`            | `42`         | RNG seed — same seed → same market.       |
| `--vol`             | `0.35`       | Annualised volatility of the synthetic series. |
| `--drift`           | `0.08`       | Annualised drift.                         |
| `--start-price`     | `100.0`      | Starting price.                           |
| `--equity`          | `100000`     | Starting account equity ($).              |
| `--risk-per-trade`  | `0.0075`     | Fraction of equity risked per trade.      |
| `--threshold`       | `0.50`       | Minimum strategy score to take a trade.   |
| `--adx-min`         | `22.0`       | Minimum ADX for trend trades.             |
| `--out`             | `reports`    | Output directory for the report bundle.   |

`simulate` adds:

| Option   | Default | Meaning                                            |
| -------- | ------: | -------------------------------------------------- |
| `--speed`| `120`   | Bars per second for the live dashboard playback.   |

`backtest` adds `--save / --no-save` to pickle the `BacktestResult` next to
the report so it can be re-rendered later via `report PATH/result.pkl`.

---

## The live dashboard

`simulate` opens a Rich-powered layout that updates in place while the
bot trades:

```
╭─ Status ────────────────────────────────────────────────────────────────╮
│ TradeBot · MOCK · 2025-01-08 14:32:00                                   │
│ Equity $103,408.14  (+3.41%)   Bars 6240/7800                           │
╰─────────────────────────────────────────────────────────────────────────╯
╭─ Price ──────────────────╮ ╭─ Signal confluence ────────────────────────╮
│ Last  $103.42            │ │ Signal     Vote   Bar                      │
│ VWAP  $102.78  ATR 0.31  │ │ ema_stack +1.00   ▶ ████████████           │
│ ▂▃▃▄▄▅▆▆▆▆▆▇▇█▇▇▇▆▆▆▆... │ │ macd      +0.74   ▶ █████████              │
│ O 103.40 H 103.55 L ...  │ │ ...                                        │
╰──────────────────────────╯ │ Score +0.612   reason: long_confluence     │
╭─ Position ───────────────╮ ╰────────────────────────────────────────────╯
│ LONG  qty 235            │ ╭─ Trade log ────────────────────────────────╮
│ Entry  $103.05           │ │  47 L 102.80→103.34 (12b) +$126.83 take_…  │
│ Stop   $102.74           │ │  48 S 104.21→103.95  (4b)  +$54.18  stop   │
│ TP     $103.74           │ │  ...                                       │
│ Risk   $72.85            │ ╰────────────────────────────────────────────╯
│ Unrealised  +$86.95      │
╰──────────────────────────╯
```

* **Status** — symbol, current bar timestamp, mark-to-market equity,
  return %, progress.
* **Price** — last/VWAP/ATR, an 80-bar sparkline, OHLCV of the current bar.
* **Signal confluence** — every indicator's vote with magnitude bar plus
  the aggregate score and the **reason** the bot did or didn't trade.
* **Position** — side, entry, live stop, take-profit, dollars at risk and
  unrealised P&L.
* **Trade log** — last 8 closed trades with side, fill prices, bars held,
  P&L and the exit reason.
* **Equity** — sparkline + exact equity & return.
* **Progress** — overall bar position.

---

## The report

After every run, `reports/<symbol>_seed<seed>_<days>d/` contains:

```
report.md        markdown summary with all metrics and the last 10 trades
equity_curve.png equity + drawdown plot
trades.csv       one row per closed trade
decisions.csv    one row per bar — score, votes, RSI/ADX/ATR/VWAP, equity
```

Metrics computed:

* **Total return %**, ending equity.
* **Sharpe** and **Sortino**, both annualised using the bar cadence.
* **Max drawdown %** computed over the equity curve.
* **Trade count, win rate, profit factor, expectancy/trade, avg win/loss**.
* **Average bars held**, longs vs. shorts, exit-reason breakdown
  (stops vs. take-profits vs. session-close).

The exit-reason breakdown is the easiest sanity check: a healthy run has
roughly equal stops and take-profits with a non-trivial number of
session-close exits coming from in-the-money runners (the trailing stop
should usually grab those).

---

## Tuning recipes

| If the bot…                            | Try                                      |
| -------------------------------------- | ---------------------------------------- |
| Trades way too often / scalpy          | `--threshold 0.6`, raise `min_agreeing_signals` to 6 in `StrategyConfig`. |
| Misses obvious moves                   | `--threshold 0.4`, lower `adx_min` to 18. |
| Too many stop-outs                     | Raise `stop_atr_mult` to 2.0 and `rr_ratio` accordingly (`RiskConfig`). |
| Drawdowns scare you                    | Lower `risk_per_trade` to 0.005 and `daily_loss_limit_pct` to 0.015. |
| Whipsaw on choppy days                 | Raise `warmup_bars` to 30, raise `post_trade_cooldown` to 5. |

For deeper edits, the four config dataclasses are the only knobs you need:

* `tradebot.market.MarketConfig` — synthetic market shape.
* `tradebot.strategy.StrategyConfig` — entry rules, weights.
* `tradebot.risk.RiskConfig` — sizing, stops, halts.
* `tradebot.engine.ExecConfig` — slippage and commissions.

---

## Project layout

```
tradebot/
├── __init__.py
├── __main__.py        # `python -m tradebot` entry point
├── cli.py             # click-based commands & argument parsing
├── market.py          # synthetic OHLCV generator (GBM + regimes + jumps)
├── indicators.py      # vectorised TA: EMA/RSI/MACD/BB/ATR/VWAP/ADX/Stoch/OBV
├── strategy.py        # multi-signal confluence + decision rules
├── risk.py            # position sizing, stops, halts
├── engine.py          # bar-by-bar mock broker + backtest loop
├── report.py          # metrics + markdown + matplotlib equity plot
└── dashboard.py       # rich-terminal live dashboard
```

---

## Honest caveats

* **The market is fake.** The generator is realistic enough for parameter
  sweeps but contains *no* news, gaps, halts, auctions, microstructure,
  or correlation with anything real. Edge measured here does not transfer
  to live trading.
* **Costs are stylised.** Real spreads widen at the open/close, fee tiers
  vary by venue, and borrow costs for shorts are ignored.
* **Single symbol, no portfolio effects.** No correlation hedging, no
  beta-neutralisation, no position rotation across names.

This bot is a **pedagogical sandbox** for thinking about confluence-based
day-trading rules, not a substitute for a real research stack.
