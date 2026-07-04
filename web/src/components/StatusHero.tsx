import { useEffect, useState } from "react";
import { AccountInfo, StrategyState } from "../api";

interface Props {
  account: AccountInfo | null;
  strategy: StrategyState | null;
}

function fmtWhen(d: Date) {
  return d.toLocaleString("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  }) + " ET";
}

function fmtCountdown(target: Date, now: Date) {
  const ms = target.getTime() - now.getTime();
  if (ms <= 0) return "now";
  const totalMin = Math.floor(ms / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return `${h}h ${m}m`;
}

function secondsAgo(ts: string, now: Date) {
  return Math.max(0, Math.floor((now.getTime() - new Date(ts).getTime()) / 1000));
}

export function StatusHero({ account, strategy }: Props) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const market = strategy?.market;
  const isOpen = market?.is_open ?? account?.is_market_open ?? false;
  const mode = strategy?.mode ?? account?.mode ?? "paper";

  let marketLine: string;
  if (market?.next_open && !isOpen) {
    marketLine = `opens ${fmtWhen(new Date(market.next_open))} — in ${fmtCountdown(new Date(market.next_open), now)}`;
  } else if (market?.next_close && isOpen) {
    marketLine = `closes ${fmtWhen(new Date(market.next_close))} — in ${fmtCountdown(new Date(market.next_close), now)}`;
  } else {
    marketLine = isOpen ? "open" : "closed";
  }

  let engineState: string;
  if (strategy?.kill_switch) {
    engineState = "HALTED: kill switch engaged";
  } else if (strategy?.halted_today && strategy.halted_reason) {
    engineState = `HALTED: ${strategy.halted_reason}`;
  } else if (!strategy?.running) {
    engineState = "STOPPED";
  } else if (!isOpen) {
    engineState = "ARMED — waiting for market open";
  } else if (strategy.last_tick) {
    engineState = `SCANNING — last tick ${secondsAgo(strategy.last_tick, now)}s ago`;
  } else {
    engineState = "SCANNING — waiting for first tick";
  }

  return (
    <div className="card status-hero">
      <div className="card-b status-hero-b">
        <span className={`badge ${isOpen ? "market-open" : "market-closed"}`}>
          {isOpen ? "MARKET OPEN" : "MARKET CLOSED"}
        </span>
        <span className="dim mono" style={{ fontSize: 12 }}>{marketLine}</span>
        <span className="status-hero-sep" />
        <span className="status-hero-engine">{engineState}</span>
        <span className={`badge ${mode}`}>● {mode.toUpperCase()}</span>
      </div>
      {strategy?.narrative && (
        <div className="status-hero-narrative">{strategy.narrative}</div>
      )}
    </div>
  );
}
