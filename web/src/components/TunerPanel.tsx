import { useState } from "react";
import { TunerProposal } from "../api";

interface Props {
  proposals: TunerProposal[];
  onRun: () => Promise<void>;
  onApply: (id: number) => Promise<void>;
}

const fmt = (n?: number) => (n === undefined || n === null ? "—" : n.toFixed(3));

export function TunerPanel({ proposals, onRun, onApply }: Props) {
  const [running, setRunning] = useState(false);
  const [applyingId, setApplyingId] = useState<number | null>(null);

  const sorted = [...proposals].sort(
    (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime(),
  );

  const runNow = async () => {
    setRunning(true);
    try {
      await onRun();
    } finally {
      setRunning(false);
    }
  };

  const apply = async (id: number) => {
    setApplyingId(id);
    try {
      await onApply(id);
    } finally {
      setApplyingId(null);
    }
  };

  return (
    <div className="card">
      <div className="card-h">
        Auto-Tuner
        <div className="right">
          <button className="primary" disabled={running} onClick={runNow}>
            {running ? "Running…" : "Run now"}
          </button>
        </div>
      </div>
      {sorted.length === 0 ? (
        <div className="empty">No proposals yet — runs nightly after close.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th className="num">Thr</th>
              <th className="num">ADX</th>
              <th className="num">Stop</th>
              <th className="num">R:R</th>
              <th className="num">Exp</th>
              <th className="num">PF</th>
              <th className="num">Trades</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => {
              const params = p.params ?? {};
              const metrics = p.metrics ?? {};
              return (
                <tr key={p.id}>
                  <td className="dim">{new Date(p.ts).toLocaleString()}</td>
                  <td className="num">{fmt(params.entry_threshold ?? params.threshold)}</td>
                  <td className="num">{fmt(params.adx_min ?? params.adx)}</td>
                  <td className="num">{fmt(params.stop_atr_mult ?? params.stop)}</td>
                  <td className="num">{fmt(params.rr_ratio ?? params.rr)}</td>
                  <td className={`num ${(metrics.expectancy ?? 0) >= 0 ? "green" : "red"}`}>
                    {fmt(metrics.expectancy)}
                  </td>
                  <td className="num">{fmt(metrics.profit_factor ?? metrics.pf)}</td>
                  <td className="num">{metrics.trades ?? metrics.n_trades ?? "—"}</td>
                  <td>
                    {p.applied ? (
                      <span className="tag filled">applied</span>
                    ) : (
                      <button
                        className="ghost"
                        disabled={applyingId === p.id}
                        onClick={() => apply(p.id)}
                      >
                        {applyingId === p.id ? "…" : "Apply"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
