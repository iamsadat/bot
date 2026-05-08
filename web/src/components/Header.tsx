import { AccountInfo, Mode, StrategyState } from "../api";

interface Props {
  account: AccountInfo | null;
  strategy: StrategyState | null;
  onKill: () => void;
  onRelease: () => void;
  onSwitchMode: (m: Mode) => void;
  onOpenSetup: () => void;
  livePrice: number | null;
  chartSymbol: string;
  streamSubs: string[];
}

export function Header({
  account, strategy, onKill, onRelease, onSwitchMode, onOpenSetup,
  livePrice, chartSymbol, streamSubs,
}: Props) {
  const mode = strategy?.mode ?? account?.mode ?? "paper";
  const running = strategy?.running ?? false;
  const killed = strategy?.kill_switch ?? false;
  const marketOpen = account?.is_market_open ?? false;
  const liveSubscribed = streamSubs.includes(chartSymbol);

  return (
    <div className="header">
      <h1>TRADEBOT</h1>

      <span
        className={`badge ${mode}`}
        title="Click to switch mode"
        style={{ cursor: "pointer" }}
        onClick={() => onSwitchMode(mode === "paper" ? "live" : "paper")}
      >
        ● {mode.toUpperCase()}
      </span>
      <span className={`badge ${marketOpen ? "market-open" : "market-closed"}`}>
        {marketOpen ? "MARKET OPEN" : "MARKET CLOSED"}
      </span>
      <span className={`badge ${running ? "running" : "stopped"}`}>
        ENGINE {running ? "ARMED" : "OFF"}
      </span>
      <span
        className="badge"
        title={liveSubscribed ? "Streaming live ticks for the chart" : "No live stream"}
        style={{ color: liveSubscribed ? "var(--green)" : "var(--text-dim)",
                 borderColor: liveSubscribed ? "#224a37" : "var(--border)" }}
      >
        {liveSubscribed ? `● LIVE ${chartSymbol}` : "○ STREAM OFF"}
      </span>
      {livePrice != null && (
        <span className="badge mono" style={{ color: "var(--text)" }}>
          {chartSymbol} {livePrice.toFixed(2)}
        </span>
      )}
      {killed && <span className="badge" style={{ color: "var(--red)", borderColor: "#582025" }}>KILL ENGAGED</span>}

      <div className="spacer" />

      <button className="ghost" onClick={onOpenSetup} title="Connect to Alpaca">
        ⚙ Setup
      </button>

      {!killed ? (
        <button className="kill-btn" onClick={onKill} title="Cancel everything, flatten everything, halt engine">
          ⚠ KILL ALL
        </button>
      ) : (
        <button className="kill-btn released" onClick={onRelease}>
          RELEASE KILL
        </button>
      )}
    </div>
  );
}
