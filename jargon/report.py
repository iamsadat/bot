"""Performance reporting.

Computes the standard set of trading metrics (Sharpe, Sortino, max drawdown,
profit factor, win rate, expectancy, …), renders a markdown report and saves
an equity-curve PNG alongside it.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .engine import BacktestResult  # noqa: E402


# ---------- metrics --------------------------------------------------------

def _bars_per_year(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    median_dt = equity.index.to_series().diff().median()
    if pd.isna(median_dt) or median_dt.total_seconds() == 0:
        return 0.0
    seconds_per_year = 365.25 * 24 * 3600
    return seconds_per_year / median_dt.total_seconds()


def compute_metrics(result: BacktestResult) -> dict:
    eq = result.equity_curve
    rets = eq.pct_change().dropna()
    bpy = _bars_per_year(eq)

    if len(rets) > 1 and rets.std(ddof=0) > 0:
        sharpe = float(rets.mean() / rets.std(ddof=0) * np.sqrt(bpy))
    else:
        sharpe = 0.0

    downside = rets[rets < 0]
    if len(downside) > 1 and downside.std(ddof=0) > 0:
        sortino = float(rets.mean() / downside.std(ddof=0) * np.sqrt(bpy))
    else:
        sortino = 0.0

    running_peak = eq.cummax()
    drawdown = eq / running_peak - 1.0
    max_dd = float(drawdown.min()) if not drawdown.empty else 0.0

    pnl = np.array([t.pnl for t in result.trades], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    n = len(pnl)
    win_rate = (len(wins) / n) if n else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    profit_factor = (
        float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    )
    expectancy = float(pnl.mean()) if n else 0.0

    starting_eq = result.risk_cfg.starting_equity
    ending_eq = float(eq.iloc[-1]) if len(eq) else starting_eq
    total_return = ending_eq / starting_eq - 1.0

    avg_bars_held = (
        float(np.mean([t.bars_held for t in result.trades])) if n else 0.0
    )

    return {
        "starting_equity": starting_eq,
        "ending_equity": ending_eq,
        "total_return_pct": total_return * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": max_dd * 100,
        "trades": n,
        "win_rate_pct": win_rate * 100,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_bars_held": avg_bars_held,
        "longs": int(sum(1 for t in result.trades if t.direction > 0)),
        "shorts": int(sum(1 for t in result.trades if t.direction < 0)),
        "stops_hit": int(sum(1 for t in result.trades if t.reason == "stop")),
        "tp_hit": int(sum(1 for t in result.trades if t.reason == "take_profit")),
        "session_close_exits": int(
            sum(1 for t in result.trades if t.reason == "session_close")
        ),
    }


# ---------- plotting -------------------------------------------------------

def save_equity_plot(result: BacktestResult, path: Path) -> None:
    eq = result.equity_curve
    running_peak = eq.cummax()
    drawdown = eq / running_peak - 1.0

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 6), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    ax1.plot(eq.index, eq.values, color="#1f77b4", linewidth=1.4, label="Equity")
    ax1.axhline(result.risk_cfg.starting_equity, color="grey", linestyle="--",
                linewidth=0.8, label="Start")
    ax1.fill_between(eq.index, eq.values, result.risk_cfg.starting_equity,
                     where=eq.values >= result.risk_cfg.starting_equity,
                     color="#1f77b4", alpha=0.08)
    ax1.set_ylabel("Equity ($)")
    ax1.set_title(f"TradeBot — {result.symbol} backtest equity curve")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)

    ax2.fill_between(drawdown.index, drawdown.values * 100, 0,
                     color="#d62728", alpha=0.4)
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Time")
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


# ---------- markdown writer ------------------------------------------------

def _fmt(v, spec=",.2f"):
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return "n/a"
    if isinstance(v, (int, float)):
        return format(v, spec)
    return str(v)


def render_markdown(result: BacktestResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = compute_metrics(result)
    plot_path = out_dir / "equity_curve.png"
    save_equity_plot(result, plot_path)

    md_path = out_dir / "report.md"
    trades_path = out_dir / "trades.csv"
    decisions_path = out_dir / "decisions.csv"

    if result.trades:
        trades_df = pd.DataFrame([{
            "direction": "LONG" if t.direction > 0 else "SHORT",
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "qty": t.qty,
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "reason": t.reason,
            "score": t.score,
            "bars_held": t.bars_held,
        } for t in result.trades])
        trades_df.to_csv(trades_path, index=False)
    else:
        trades_path.write_text("no trades recorded\n")
    result.decisions.to_csv(decisions_path)

    sc = result.strategy_cfg
    rc = result.risk_cfg

    lines: list[str] = []
    lines.append(f"# TradeBot Report — {result.symbol}")
    lines.append("")
    lines.append(f"_Period_: {result.equity_curve.index[0]} → "
                 f"{result.equity_curve.index[-1]}  ")
    lines.append(f"_Bars_: {len(result.equity_curve):,}  ")
    lines.append(f"_Trades_: {metrics['trades']:,}")
    lines.append("")

    lines.append("## Headline metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | ---: |")
    lines.append(f"| Starting equity | ${_fmt(metrics['starting_equity'])} |")
    lines.append(f"| Ending equity | ${_fmt(metrics['ending_equity'])} |")
    lines.append(f"| Total return | {_fmt(metrics['total_return_pct'])}% |")
    lines.append(f"| Sharpe (annualised) | {_fmt(metrics['sharpe'])} |")
    lines.append(f"| Sortino (annualised) | {_fmt(metrics['sortino'])} |")
    lines.append(f"| Max drawdown | {_fmt(metrics['max_drawdown_pct'])}% |")
    lines.append(f"| Win rate | {_fmt(metrics['win_rate_pct'])}% |")
    lines.append(f"| Profit factor | {_fmt(metrics['profit_factor'])} |")
    lines.append(f"| Expectancy / trade | ${_fmt(metrics['expectancy'])} |")
    lines.append(f"| Avg win / loss | ${_fmt(metrics['avg_win'])} / "
                 f"${_fmt(metrics['avg_loss'])} |")
    lines.append(f"| Avg bars held | {_fmt(metrics['avg_bars_held'])} |")
    lines.append(f"| Longs / Shorts | {metrics['longs']} / {metrics['shorts']} |")
    lines.append(f"| Exits — stops / TP / EOD | {metrics['stops_hit']} / "
                 f"{metrics['tp_hit']} / {metrics['session_close_exits']} |")
    lines.append("")

    lines.append("## Equity curve")
    lines.append("")
    lines.append(f"![equity curve]({plot_path.name})")
    lines.append("")

    lines.append("## Configuration")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps({
        "strategy": asdict(sc),
        "risk": asdict(rc),
    }, indent=2, default=str))
    lines.append("```")
    lines.append("")

    if result.trades:
        lines.append("## Last 10 trades")
        lines.append("")
        lines.append("| # | Side | Entry | Exit | Qty | PnL | Reason | Score |")
        lines.append("| ---: | --- | --- | --- | ---: | ---: | --- | ---: |")
        for i, t in enumerate(result.trades[-10:], 1):
            side = "LONG" if t.direction > 0 else "SHORT"
            lines.append(
                f"| {i} | {side} | {t.entry_time:%m-%d %H:%M} @ {t.entry_price:.2f}"
                f" | {t.exit_time:%m-%d %H:%M} @ {t.exit_price:.2f}"
                f" | {t.qty} | ${t.pnl:,.2f} | {t.reason} | {t.score:+.2f} |"
            )
        lines.append("")

    lines.append("## Files")
    lines.append("")
    lines.append(f"- [`equity_curve.png`]({plot_path.name})")
    lines.append(f"- [`trades.csv`]({trades_path.name})")
    lines.append(f"- [`decisions.csv`]({decisions_path.name}) — "
                 "per-bar scores, reasons, indicators")
    lines.append("")

    md_path.write_text("\n".join(lines))
    return md_path
