"""Rich-terminal live dashboard.

Renders the bot's view of the world while a backtest streams through:
header status, price sparkline, indicator votes, open position card,
P&L summary, and a rolling trade log.
"""

from __future__ import annotations

from collections import deque
from typing import Deque

from rich.align import Align
from rich.box import ROUNDED
from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text


SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values, width: int = 60) -> str:
    if not values:
        return ""
    vs = list(values)[-width:]
    lo, hi = min(vs), max(vs)
    if hi == lo:
        return SPARK_BLOCKS[0] * len(vs)
    rng = hi - lo
    step = len(SPARK_BLOCKS) - 1
    return "".join(SPARK_BLOCKS[int((v - lo) / rng * step)] for v in vs)


def _color_pnl(value: float) -> str:
    if value > 0:
        return f"[bold green]+${value:,.2f}[/]"
    if value < 0:
        return f"[bold red]-${abs(value):,.2f}[/]"
    return f"[dim]${value:,.2f}[/]"


def _color_pct(value: float) -> str:
    if value > 0:
        return f"[bold green]+{value:.2f}%[/]"
    if value < 0:
        return f"[bold red]{value:.2f}%[/]"
    return f"[dim]{value:.2f}%[/]"


class Dashboard:
    """Encapsulates the live layout.  Call ``update`` from the engine hook."""

    def __init__(self, total_bars: int, symbol: str, start_equity: float):
        self.total_bars = total_bars
        self.symbol = symbol
        self.start_equity = start_equity
        self.price_history: Deque[float] = deque(maxlen=80)
        self.equity_history: Deque[float] = deque(maxlen=80)
        self.last_event = ""
        self._live: Live | None = None
        self._progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total} bars"),
        )
        self._task_id = self._progress.add_task("Streaming", total=total_bars)

    # -- entry / exit ------------------------------------------------------

    def __enter__(self):
        self._live = Live(self._render({}), refresh_per_second=10, screen=False)
        self._live.__enter__()
        return self

    def __exit__(self, *exc):
        if self._live is not None:
            self._live.__exit__(*exc)
            self._live = None

    # -- rendering ---------------------------------------------------------

    def _header(self, ctx: dict) -> Panel:
        ts = ctx.get("ts")
        equity = ctx.get("equity", self.start_equity)
        ret_pct = (equity / self.start_equity - 1) * 100
        i = ctx.get("i", 0) + 1
        head = Text.from_markup(
            f"[bold cyan]TradeBot[/] · [bold]{self.symbol}[/] · "
            f"[dim]{ts if ts is not None else ''}[/]\n"
            f"[bold]Equity[/] [white]${equity:,.2f}[/]  "
            f"({_color_pct(ret_pct)})   "
            f"[bold]Bars[/] [white]{i}/{self.total_bars}[/]"
        )
        return Panel(head, box=ROUNDED, border_style="cyan", title="Status")

    def _price_panel(self, ctx: dict) -> Panel:
        row = ctx.get("row")
        if row is None:
            return Panel("", title="Price", box=ROUNDED, border_style="blue")
        spark = sparkline(self.price_history, width=70)
        body = Text()
        body.append(f"Last  ${row['close']:,.2f}\n", style="bold white")
        body.append(f"VWAP  ${row['vwap']:,.2f}   "
                    f"ATR  {row['atr']:.3f}\n",
                    style="dim")
        body.append(spark + "\n", style="bright_blue")
        body.append(
            f"O {row['open']:.2f}  H {row['high']:.2f}  "
            f"L {row['low']:.2f}  V {int(row['volume']):,}",
            style="dim",
        )
        return Panel(body, title="Price", box=ROUNDED, border_style="blue")

    def _signals_panel(self, ctx: dict) -> Panel:
        decision = ctx.get("decision")
        table = Table(box=None, show_header=True, header_style="bold")
        table.add_column("Signal", style="white")
        table.add_column("Vote", justify="right")
        table.add_column("Bar", justify="left")

        votes = decision.votes if decision else {}
        for name in ("ema_stack", "macd", "adx_dir", "rsi", "stoch",
                     "vwap_dev", "bb_pos", "obv"):
            v = votes.get(name, 0.0)
            colour = "green" if v > 0 else ("red" if v < 0 else "dim")
            bar_len = int(abs(v) * 12)
            bar = ("█" * bar_len).ljust(12)
            sign = "▶" if v >= 0 else "◀"
            table.add_row(name, f"[{colour}]{v:+.2f}[/]",
                          f"[{colour}]{sign} {bar}[/]")

        score_line = Text()
        if decision is not None:
            score_line.append("Score ", style="bold")
            score_colour = ("green" if decision.score > 0
                            else "red" if decision.score < 0 else "white")
            score_line.append(f"{decision.score:+.3f}", style=f"bold {score_colour}")
            score_line.append(f"   reason: {decision.reason}", style="dim")
        return Panel(Group(table, Text(""), score_line),
                     title="Signal confluence",
                     box=ROUNDED, border_style="magenta")

    def _position_panel(self, ctx: dict) -> Panel:
        plan = ctx.get("open_plan")
        row = ctx.get("row")
        if plan is None or row is None:
            body = Text("Flat — waiting for confluence", style="dim")
        else:
            side = "[bold green]LONG[/]" if plan.direction > 0 else "[bold red]SHORT[/]"
            unrealised = (row["close"] - plan.entry_price) * plan.qty * plan.direction
            body = Text.from_markup(
                f"{side}  qty {plan.qty}\n"
                f"Entry  ${plan.entry_price:,.2f}\n"
                f"Stop   ${plan.stop:,.2f}\n"
                f"TP     ${plan.take_profit:,.2f}\n"
                f"Risk   ${plan.risk_dollars:,.2f}\n"
                f"Unrealised  {_color_pnl(unrealised)}"
            )
        return Panel(body, title="Position", box=ROUNDED, border_style="yellow")

    def _trades_panel(self, ctx: dict) -> Panel:
        trades = ctx.get("trades", [])
        table = Table(box=None, show_header=True, header_style="bold")
        table.add_column("#", justify="right")
        table.add_column("Side")
        table.add_column("Entry → Exit")
        table.add_column("PnL", justify="right")
        table.add_column("Reason", style="dim")
        for i, t in enumerate(trades[-8:], start=max(1, len(trades) - 7)):
            side = "[green]L[/]" if t.direction > 0 else "[red]S[/]"
            pnl_txt = _color_pnl(t.pnl)
            arrow = (f"{t.entry_price:.2f}→{t.exit_price:.2f}"
                     f"  ({t.bars_held}b)")
            table.add_row(str(i), side, arrow, pnl_txt, t.reason)
        if not trades:
            return Panel(Text("No trades yet", style="dim"),
                         title="Trade log", box=ROUNDED, border_style="green")
        return Panel(table, title="Trade log", box=ROUNDED, border_style="green")

    def _equity_panel(self, ctx: dict) -> Panel:
        spark = sparkline(self.equity_history, width=60)
        equity = ctx.get("equity", self.start_equity)
        ret_pct = (equity / self.start_equity - 1) * 100
        spark_colour = "green" if ret_pct >= 0 else "red"
        body = Text.from_markup(
            f"[{spark_colour}]{spark}[/]\n"
            f"[bold]Equity[/] ${equity:,.2f}  {_color_pct(ret_pct)}"
        )
        return Panel(body, title="Equity", box=ROUNDED, border_style="green")

    def _progress_panel(self) -> Panel:
        return Panel(self._progress, title="Progress",
                     box=ROUNDED, border_style="cyan")

    def _render(self, ctx: dict) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="body"),
            Layout(name="footer", size=5),
        )
        layout["body"].split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=3),
        )
        layout["left"].split_column(
            Layout(self._price_panel(ctx), name="price"),
            Layout(self._position_panel(ctx), name="position"),
            Layout(self._equity_panel(ctx), name="equity"),
        )
        layout["right"].split_column(
            Layout(self._signals_panel(ctx), name="signals"),
            Layout(self._trades_panel(ctx), name="trades"),
        )
        layout["header"].update(self._header(ctx))
        layout["footer"].update(self._progress_panel())
        return layout

    # -- public hook --------------------------------------------------------

    def update(self, ctx: dict) -> None:
        row = ctx.get("row")
        if row is not None:
            self.price_history.append(float(row["close"]))
        self.equity_history.append(float(ctx.get("equity", self.start_equity)))
        self._progress.update(self._task_id, completed=ctx.get("i", 0) + 1)
        if self._live is not None:
            self._live.update(self._render(ctx))
