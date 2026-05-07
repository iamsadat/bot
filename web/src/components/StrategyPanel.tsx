import { useEffect, useState } from "react";
import { StrategyConfig, StrategyState } from "../api";

interface Props {
  strategy: StrategyState | null;
  onStart: () => void;
  onStop: () => void;
  onSave: (cfg: StrategyConfig) => Promise<void>;
}

const VOTE_KEYS = [
  "ema_stack",
  "macd",
  "adx_dir",
  "rsi",
  "stoch",
  "vwap_dev",
  "bb_pos",
  "obv",
];

export function StrategyPanel({ strategy, onStart, onStop, onSave }: Props) {
  const [cfg, setCfg] = useState<StrategyConfig | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (strategy && !dirty) setCfg(strategy.config);
  }, [strategy, dirty]);

  if (!strategy || !cfg) {
    return (
      <div className="card">
        <div className="card-h">Strategy</div>
        <div className="empty">Loading…</div>
      </div>
    );
  }

  const setField = <K extends keyof StrategyConfig>(k: K, v: StrategyConfig[K]) => {
    setCfg((c) => (c ? { ...c, [k]: v } : c));
    setDirty(true);
  };

  const decision = strategy.last_decision;
  const score = decision?.score ?? 0;
  const reason = decision?.reason ?? "—";
  const direction = decision?.direction ?? 0;
  const votes: Record<string, number> = decision?.votes ?? {};

  return (
    <div className="card">
      <div className="card-h">
        Strategy
        <div className="right btn-row">
          {strategy.running ? (
            <button className="danger" onClick={onStop}>Stop</button>
          ) : (
            <button className="success" onClick={onStart}>Start</button>
          )}
        </div>
      </div>
      <div className="card-b">
        <div className="row three">
          <div className="kpi">
            <div className="label">Score</div>
            <div className={`value ${score > 0 ? "green" : score < 0 ? "red" : "dim"}`}>
              {score >= 0 ? "+" : ""}{score.toFixed(3)}
            </div>
          </div>
          <div className="kpi">
            <div className="label">Direction</div>
            <div className={`value ${direction > 0 ? "green" : direction < 0 ? "red" : "dim"}`}>
              {direction === 0 ? "FLAT" : direction > 0 ? "LONG" : "SHORT"}
            </div>
          </div>
          <div className="kpi">
            <div className="label">Reason</div>
            <div className="value mono" style={{ fontSize: 12 }}>{reason}</div>
          </div>
        </div>

        {Object.keys(votes).length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div className="label dim mono" style={{ fontSize: 11, marginBottom: 4 }}>
              SIGNAL VOTES
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "auto auto 1fr", gap: "2px 12px", fontFamily: "var(--mono)", fontSize: 12 }}>
              {VOTE_KEYS.map((k) => {
                const v = votes[k] ?? 0;
                const w = Math.min(100, Math.abs(v) * 100);
                return (
                  <div key={k} style={{ display: "contents" }}>
                    <span className="dim">{k}</span>
                    <span className={v >= 0 ? "green" : "red"}>{v >= 0 ? "+" : ""}{v.toFixed(2)}</span>
                    <span>
                      <span className={`signal-bar ${v >= 0 ? "green" : "red"}`}
                            style={{ width: `${w}%` }} />
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "16px 0" }} />

        <div className="row">
          <div className="field">
            <label>Symbols (comma)</label>
            <input
              type="text"
              value={cfg.symbols.join(",")}
              onChange={(e) =>
                setField(
                  "symbols",
                  e.target.value.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
                )
              }
            />
          </div>
          <div className="field">
            <label>Auto-trade</label>
            <select
              value={cfg.auto_trade ? "yes" : "no"}
              onChange={(e) => setField("auto_trade", e.target.value === "yes")}
            >
              <option value="yes">YES — submit orders</option>
              <option value="no">NO — signals only</option>
            </select>
          </div>
        </div>

        <div className="row three">
          <div className="field">
            <label>Threshold</label>
            <input type="number" step="0.05" min={0} max={1}
                   value={cfg.entry_threshold}
                   onChange={(e) => setField("entry_threshold", Number(e.target.value))} />
          </div>
          <div className="field">
            <label>ADX min</label>
            <input type="number" step="1" min={0} max={60}
                   value={cfg.adx_min}
                   onChange={(e) => setField("adx_min", Number(e.target.value))} />
          </div>
          <div className="field">
            <label>Risk / trade</label>
            <input type="number" step="0.001" min={0} max={0.1}
                   value={cfg.risk_per_trade}
                   onChange={(e) => setField("risk_per_trade", Number(e.target.value))} />
          </div>
        </div>

        <div className="row three">
          <div className="field">
            <label>Stop ATR ×</label>
            <input type="number" step="0.1" min={0.5} max={5}
                   value={cfg.stop_atr_mult}
                   onChange={(e) => setField("stop_atr_mult", Number(e.target.value))} />
          </div>
          <div className="field">
            <label>R:R ratio</label>
            <input type="number" step="0.1" min={1} max={5}
                   value={cfg.rr_ratio}
                   onChange={(e) => setField("rr_ratio", Number(e.target.value))} />
          </div>
          <div className="field" style={{ alignSelf: "end" }}>
            <button
              className="primary"
              disabled={!dirty}
              onClick={async () => { await onSave(cfg); setDirty(false); }}
            >
              {dirty ? "Save changes" : "Saved"}
            </button>
          </div>
        </div>

        <div className="dim mono" style={{ fontSize: 11, marginTop: 4 }}>
          Last tick: {strategy.last_tick ? new Date(strategy.last_tick).toLocaleString() : "—"}
        </div>
      </div>
    </div>
  );
}
