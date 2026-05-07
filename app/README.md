# TradeBot — live web app

A paper-first day-trading web app built on top of the
[`tradebot`](../tradebot) confluence engine. It connects to **Alpaca**
(paper or live), exposes a clean React dashboard, and lets you both:

* run the strategy engine fully automatically, and
* place / cancel / flatten manual orders at any time.

> ⚠ **Important.** The default mode is **paper trading**. The strategy in
> `tradebot/` has only been validated on synthetic data — it has *no*
> proven edge on real markets yet. **Switching to live mode requires an
> explicit confirmation phrase** and live-account keys; do not flip it
> until you have run on paper for several weeks and reviewed the audit
> log and reports.

---

## What's in here

```
bot/
├── app/                        ← FastAPI backend (this README)
│   ├── main.py                 ← app + lifespan + WS
│   ├── config.py               ← env-driven Settings
│   ├── db.py / models.py       ← SQLite + SQLAlchemy
│   ├── deps.py                 ← broker factory
│   ├── ws.py                   ← WebSocket hub
│   ├── api/                    ← /api routes
│   │   ├── routes_account.py   ← /account /positions /market/bars
│   │   ├── routes_orders.py    ← /orders, /orders/cancel, /positions/X/close
│   │   ├── routes_strategy.py  ← /strategy, /strategy/start /stop, /strategy/config
│   │   ├── routes_safety.py    ← /safety/kill /release /mode
│   │   └── routes_audit.py     ← /audit
│   ├── brokers/
│   │   ├── base.py             ← Broker interface
│   │   └── alpaca.py           ← Alpaca paper + live adapter
│   └── trading/
│       ├── engine.py           ← async strategy loop
│       ├── risk.py             ← pre-trade gate + position sizing
│       ├── state.py            ← persistent strategy state
│       └── audit.py            ← audit log helper
├── web/                        ← React + Vite frontend
└── tradebot/                   ← decision engine (re-used)
```

---

## Quick start (paper trading)

### 1. Get an Alpaca paper account

It's free, takes 2 minutes, no funding required:

* Sign up at <https://alpaca.markets>
* Go to <https://app.alpaca.markets/paper/dashboard/overview>
* Click **View API Keys** → **Generate New Key**

### 2. Configure the app

```bash
cd bot
cp .env.example .env
# then edit .env and set:
#   ALPACA_PAPER_KEY=PK...
#   ALPACA_PAPER_SECRET=...
```

### 3. Install dependencies

```bash
# Backend (Python 3.10+ recommended)
pip install -r app/requirements.txt

# Frontend
cd web && npm install && cd ..
```

### 4. Run it

**Option A — single server (recommended for paper-trading day-to-day).**
Build the frontend once and let the FastAPI app serve it:

```bash
cd web && npm run build && cd ..
uvicorn app.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
```

**Option B — dev mode with hot-reload.** Two terminals:

```bash
# terminal 1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# terminal 2
cd web && npm run dev
# open http://127.0.0.1:5173
```

The Vite dev server proxies `/api/*` and `/ws` to the backend.

---

## Using the app

### Dashboard at a glance

| Panel             | What it shows                                                   |
| ----------------- | --------------------------------------------------------------- |
| Header            | Mode (paper/live), market open/closed, engine state, **kill button**. |
| Account           | Equity, cash, buying power, day P&L (% and $).                  |
| Positions         | Each open position with side, qty, mark, P&L, **Close** button. |
| Orders            | Recent orders (manual + strategy) with status, source, **Cancel** per row, **Cancel all open**. |
| Strategy          | Live confluence score with per-signal vote bars + last-tick reason; configurable threshold/ADX/risk/RR; start / stop / save. |
| Manual Order      | Symbol, side, qty, market / limit / bracket, with stop & TP for brackets. |
| Audit Log         | Append-only system log (orders, kills, mode changes, errors).  |

The header polls every 5 s and the WebSocket pushes any decision/order/kill
event the moment it happens.

### Auto-trading workflow

1. Set your symbol(s) and parameters in **Strategy**, save.
2. Click **Start** in the Strategy card. The engine begins an async loop,
   ticking every `ENGINE_TICK_SECONDS` (default 60 s) while the market is
   open.
3. Each tick: pull bars → compute confluence score → if score clears
   threshold AND 5/8 signals agree AND ADX/VWAP/RSI gates pass → size a
   bracket order and submit it.
4. Bracket orders carry their own stop-loss and take-profit on the broker
   side, so you are protected even if the app crashes.
5. Watch the **Audit log** for everything that happens.

### Manual workflow

The Manual Order card supports:

* **Market** — fastest, no price guarantee.
* **Limit** — hold for a price.
* **Bracket** — market entry plus stop-loss and take-profit in a single
  call. This is what the auto-trader uses too.

Every order — manual or strategy — passes through the same risk gate
before reaching the broker.

### Kill switch

The big red button in the header is the **panic button**. It:

1. Stops the engine immediately.
2. Cancels every open order via `/v2/orders` DELETE-ALL.
3. Flattens every open position via `/v2/positions` DELETE-ALL.
4. Sets the engine to "halted" until you click **Release Kill**.

Use it any time something looks wrong. It's idempotent and safe to spam.

---

## Safety model

This is the most important part. **Read it.**

### Default-deny

* Default mode is **paper**.
* Engine starts **stopped**. You have to click Start.
* `auto_trade=true` is the default but you can flip it to `false` to get
  signals without orders.

### Server-enforced rails (cannot be bypassed by the UI)

| Rail                            | Default        | What it does |
| ------------------------------- | -------------: | --- |
| `risk_per_trade`                | 0.75 % of equity | Stop-distance × qty cannot exceed this. |
| `max_position_notional_pct`     | 25 %           | qty × price cannot exceed this. |
| `daily_loss_limit_pct`          | 2.5 %          | Engine and manual entries halt for the day below this P&L. |
| `max_orders_per_minute`         | 10             | Sliding-window limit on order submissions. |
| Bracket SL/TP required          | always         | The strategy uses bracket orders, so stops are at the broker, not just in app memory. |
| Idempotency keys                | every order    | Re-submission with the same key returns the original record. |

### Switching to live mode

This is intentionally annoying:

1. **Engine must be stopped.** The UI will refuse otherwise.
2. **Live keys must be configured** (`ALPACA_LIVE_KEY` /
   `ALPACA_LIVE_SECRET`).
3. **Confirmation phrase** must match `LIVE_CONFIRMATION_PHRASE`
   (default `I_UNDERSTAND_REAL_MONEY`). The phrase is required in the
   request body of `POST /api/safety/mode`.
4. The mode change is written to the audit log with `actor=user`.

To go *back* to paper mode just click the badge — no phrase needed.

### Audit trail

Every order, cancellation, mode change, kill, error and config change
lands in the `audit` table. The Audit Log card shows the most recent 50
entries. The full table is reachable at `GET /api/audit?limit=…&kind=…`
and lives forever in `tradebot.db`.

---

## API reference

All routes live under `/api`. WebSocket is `/ws`.

| Method | Path                            | Purpose |
| ------ | ------------------------------- | --- |
| GET    | `/api/health`                   | Liveness.                                    |
| GET    | `/api/account`                  | Cash, equity, buying power, day P&L.         |
| GET    | `/api/positions`                | All open positions.                          |
| GET    | `/api/market/bars/{symbol}`     | Recent minute bars (default 240).            |
| GET    | `/api/orders`                   | Recorded orders (DB).                        |
| GET    | `/api/orders/open`              | Open orders (broker).                        |
| POST   | `/api/orders`                   | Place manual order (market/limit/bracket).   |
| POST   | `/api/orders/cancel`            | `{cancel_all:true}` or `{broker_order_id}`.  |
| POST   | `/api/positions/{symbol}/close` | Flatten a single position.                   |
| GET    | `/api/strategy`                 | Engine state + last decision.                |
| POST   | `/api/strategy/start`           | Arm the engine.                              |
| POST   | `/api/strategy/stop`            | Disarm the engine.                           |
| PUT    | `/api/strategy/config`          | Update threshold / ADX / risk / symbols.     |
| POST   | `/api/safety/kill`              | Cancel + flatten + halt.                     |
| POST   | `/api/safety/release`           | Release the kill.                            |
| POST   | `/api/safety/mode`              | `{mode, confirmation}` — switch paper↔live.  |
| GET    | `/api/audit`                    | Audit log (filterable by `kind`).            |
| WS     | `/ws`                           | Server-pushed events: `decision`, `order`, `kill`. |

OpenAPI docs are auto-generated at `http://127.0.0.1:8000/docs`.

---

## Production deploy

The simplest production setup:

```bash
# 1. Build the frontend once
(cd web && npm ci && npm run build)

# 2. Run uvicorn behind a reverse proxy (nginx / Caddy)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

A few things to pay attention to:

* **Run a single worker.** The trading engine is process-local; multiple
  workers would spawn duplicate engines and double-fire orders.
* **Persist `tradebot.db`.** If you containerise this, mount the SQLite
  file on a volume so audit history survives.
* **Never expose this publicly without auth.** This MVP has none — anyone
  who reaches `/api/safety/mode` can flip your account into live trading
  and start orders. Run behind your VPN, or stick `caddy basic_auth` /
  `oauth2-proxy` in front.
* **Use `systemd` or similar** for restart-on-crash, and route the audit
  log somewhere durable.

---

## Troubleshooting

| Symptom                                | Likely cause / fix |
| -------------------------------------- | --- |
| `503 Alpaca paper keys not set`        | Edit `.env`, restart the API. |
| Account/positions OK but engine never trades | Outside market hours; confluence not met (look at `last_decision.reason`); `auto_trade=false` in config. |
| All orders rejected with `notional_exceeds_max_position_pct` | Lower `qty` or raise `MAX_POSITION_NOTIONAL_PCT`. |
| `daily_loss_limit_hit`                 | The day-loss circuit breaker tripped. It auto-resets next session. |
| Orders fill but no stop / TP visible   | Check that you used `type=bracket` (or that the strategy did — it always does). Manual market orders never have a stop. |
| Frontend shows nothing past the header | Backend on a different port. Run `npm run dev` *or* `npm run build` + serve from the API. |

---

## Honest caveats (still applicable)

* **The strategy is unvalidated on real ticks.** Synthetic Sharpe ≠ live
  Sharpe. Run paper for *weeks*, not hours, before considering live.
* **No portfolio diversification.** Single-symbol decisions; no
  correlation handling, no leverage management beyond Alpaca's defaults.
* **No news/events.** The model has no idea about earnings, FOMC days,
  halts. You should manually disable the engine ahead of known events.
* **MVP auth.** This build assumes you are the only user on a localhost
  or VPN-fronted deployment. Do **not** put it on the open internet
  without an auth proxy.
* **One process, one engine.** Don't run two uvicorn workers; you'll
  double-fire orders.
