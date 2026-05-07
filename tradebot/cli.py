"""Command-line entry point.

Three subcommands:

  * ``backtest``  — fast, headless run; writes a markdown report.
  * ``simulate``  — same engine, slowed down with a live rich dashboard.
  * ``report``    — render a report from a previously persisted run
                    (the engine pickles its result if --save is passed).

Run ``python -m tradebot --help`` for full options.
"""

from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import click
import pandas as pd
from rich.console import Console
from rich.table import Table

from .dashboard import Dashboard
from .engine import ExecConfig, run_backtest
from .market import MarketConfig, generate
from .report import compute_metrics, render_markdown
from .risk import RiskConfig
from .strategy import StrategyConfig


CONSOLE = Console()


def _build_market(symbol, days, seed, vol, drift, start_price):
    return generate(MarketConfig(
        symbol=symbol,
        days=days,
        seed=seed,
        annual_vol=vol,
        annual_drift=drift,
        start_price=start_price,
    ))


def _summary_table(metrics: dict) -> Table:
    t = Table(title="Backtest summary", show_lines=False)
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")

    fmt = lambda v, s=",.2f": format(v, s) if isinstance(v, (int, float)) else str(v)

    t.add_row("Starting equity", f"${fmt(metrics['starting_equity'])}")
    t.add_row("Ending equity", f"${fmt(metrics['ending_equity'])}")
    ret = metrics["total_return_pct"]
    t.add_row("Total return",
              f"[{'green' if ret >= 0 else 'red'}]{fmt(ret)}%[/]")
    t.add_row("Sharpe (ann.)", fmt(metrics["sharpe"]))
    t.add_row("Sortino (ann.)", fmt(metrics["sortino"]))
    t.add_row("Max drawdown",
              f"[red]{fmt(metrics['max_drawdown_pct'])}%[/]")
    t.add_row("Trades", str(metrics["trades"]))
    t.add_row("Win rate", f"{fmt(metrics['win_rate_pct'])}%")
    t.add_row("Profit factor", fmt(metrics["profit_factor"]))
    t.add_row("Expectancy / trade", f"${fmt(metrics['expectancy'])}")
    t.add_row("Avg win / loss",
              f"${fmt(metrics['avg_win'])} / ${fmt(metrics['avg_loss'])}")
    return t


# ---------- common option group -------------------------------------------

def common_options(f):
    f = click.option("--symbol", default="MOCK", help="Ticker label.")(f)
    f = click.option("--days", default=20, show_default=True,
                     help="Number of synthetic trading days.")(f)
    f = click.option("--seed", default=42, show_default=True,
                     help="RNG seed for reproducibility.")(f)
    f = click.option("--vol", default=0.35, show_default=True,
                     help="Annualised volatility.")(f)
    f = click.option("--drift", default=0.08, show_default=True,
                     help="Annualised drift.")(f)
    f = click.option("--start-price", default=100.0, show_default=True)(f)
    f = click.option("--equity", default=100_000.0, show_default=True,
                     help="Starting equity.")(f)
    f = click.option("--risk-per-trade", default=0.0075, show_default=True,
                     help="Fraction of equity risked per trade.")(f)
    f = click.option("--threshold", default=0.50, show_default=True,
                     help="Strategy entry score threshold.")(f)
    f = click.option("--adx-min", default=22.0, show_default=True,
                     help="Minimum ADX for trend trades.")(f)
    f = click.option("--out", default="reports", show_default=True,
                     type=click.Path(file_okay=False),
                     help="Output directory for reports.")(f)
    return f


@click.group()
def cli():
    """TradeBot — mock day-trading bot with multi-signal confluence."""


@cli.command()
@common_options
@click.option("--save/--no-save", default=False,
              help="Pickle the BacktestResult next to the report.")
def backtest(symbol, days, seed, vol, drift, start_price,
             equity, risk_per_trade, threshold, adx_min, out, save):
    """Run a fast headless backtest and write a markdown report."""
    bars = _build_market(symbol, days, seed, vol, drift, start_price)
    strat = StrategyConfig(entry_threshold=threshold, adx_min=adx_min)
    risk = RiskConfig(starting_equity=equity, risk_per_trade=risk_per_trade)

    CONSOLE.print(
        f"[cyan]Running backtest[/]: {symbol}  "
        f"{days} days · {len(bars):,} bars · seed {seed}"
    )

    result = run_backtest(bars, strat, risk, ExecConfig())
    metrics = compute_metrics(result)

    out_dir = Path(out) / f"{symbol}_seed{seed}_{days}d"
    md_path = render_markdown(result, out_dir)
    if save:
        with open(out_dir / "result.pkl", "wb") as fh:
            pickle.dump(result, fh)

    CONSOLE.print(_summary_table(metrics))
    CONSOLE.print(f"[green]Report written to[/] {md_path}")


@cli.command()
@common_options
@click.option("--speed", default=120.0, show_default=True,
              help="Bars per second for the live dashboard.")
def simulate(symbol, days, seed, vol, drift, start_price,
             equity, risk_per_trade, threshold, adx_min, out, speed):
    """Stream a backtest live in the terminal with a rich dashboard."""
    bars = _build_market(symbol, days, seed, vol, drift, start_price)
    strat = StrategyConfig(entry_threshold=threshold, adx_min=adx_min)
    risk = RiskConfig(starting_equity=equity, risk_per_trade=risk_per_trade)
    delay = 1.0 / max(speed, 1.0)

    with Dashboard(total_bars=len(bars), symbol=symbol,
                   start_equity=equity) as dash:
        def _hook(ctx: dict):
            dash.update(ctx)
            if delay > 0:
                time.sleep(delay)
        result = run_backtest(bars, strat, risk, ExecConfig(), on_bar=_hook)

    metrics = compute_metrics(result)
    out_dir = Path(out) / f"{symbol}_seed{seed}_{days}d_sim"
    md_path = render_markdown(result, out_dir)
    CONSOLE.print(_summary_table(metrics))
    CONSOLE.print(f"[green]Report written to[/] {md_path}")


@cli.command()
@click.argument("pickle_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--out", default="reports", show_default=True,
              type=click.Path(file_okay=False))
def report(pickle_path, out):
    """Re-render a markdown report from a pickled BacktestResult."""
    with open(pickle_path, "rb") as fh:
        result = pickle.load(fh)
    out_dir = Path(out) / Path(pickle_path).stem
    md_path = render_markdown(result, out_dir)
    CONSOLE.print(_summary_table(compute_metrics(result)))
    CONSOLE.print(f"[green]Report written to[/] {md_path}")


if __name__ == "__main__":
    sys.exit(cli())
