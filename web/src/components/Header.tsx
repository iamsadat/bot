import { AccountInfo, Mode, StrategyState } from "../api";

interface Props {
  account: AccountInfo | null;
  strategy: StrategyState | null;
  onKill: () => void;
  onRelease: () => void;
  onSwitchMode: (m: Mode) => void;
}

export function Header({ account, strategy, onKill, onRelease, onSwitchMode }: Props) {
  const mode = strategy?.mode ?? account?.mode ?? "paper";
  const running = strategy?.running ?? false;
  const killed = strategy?.kill_switch ?? false;
  const marketOpen = account?.is_market_open ?? false;

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
      {killed && <span className="badge" style={{ color: "var(--red)", borderColor: "#582025" }}>KILL ENGAGED</span>}

      <div className="spacer" />

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
