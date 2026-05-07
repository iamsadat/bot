"""Application configuration.

All sensitive values come from environment variables (or a .env file).
The default mode is *paper* — switching to *live* requires both:
  1. Live-mode API keys to be present in the environment.
  2. An explicit POST /api/safety/mode call with the confirmation phrase.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Server -----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = str(REPO_ROOT / "tradebot.db")
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # -- Broker (Alpaca) --------------------------------------------------
    alpaca_paper_key: str | None = Field(default=None)
    alpaca_paper_secret: str | None = Field(default=None)
    alpaca_live_key: str | None = Field(default=None)
    alpaca_live_secret: str | None = Field(default=None)

    # -- Trading defaults -------------------------------------------------
    default_mode: Literal["paper", "live"] = "paper"
    default_symbol: str = "SPY"

    # -- Risk rails (server-enforced, not just client UI) -----------------
    risk_per_trade: float = 0.0075          # 0.75 % equity at risk per trade
    max_position_notional_pct: float = 0.25 # ≤ 25 % of equity per position
    daily_loss_limit_pct: float = 0.025     # halt at –2.5 % day P&L
    max_orders_per_minute: int = 10
    stop_atr_mult: float = 1.5
    rr_ratio: float = 2.0

    # -- Engine cadence ---------------------------------------------------
    engine_tick_seconds: int = 60           # minute bars
    reconcile_seconds: int = 30

    # -- Live-mode confirmation ------------------------------------------
    live_confirmation_phrase: str = "I_UNDERSTAND_REAL_MONEY"


settings = Settings()
