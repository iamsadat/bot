import { Trade } from "../api";

interface Props {
  trades: Trade[];
}

const fmt$ = (n: number) =>
  `${n >= 0 ? "+" : ""}${n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 })}`;

const fmtTs = (ts: string) => new Date(ts).toLocaleString([], {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
});

export function TradesPanel({ trades }: Props) {
  const wins = trades.filter((t) => t.pnl > 0);
  const losses = trades.filter((t) => t.pnl < 0);
  const winRate = trades.length ? (wins.length / trades.length) * 100 : 0;
  const totalPnl = trades.reduce((s, t) => s + t.pnl, 0);
  const grossProfit = wins.reduce((s, t) => s + t.pnl, 0);
  const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

  return (
    <div className="card">
      <div className="card-h">
        Closed Trades <span className="dim mono right">{trades.length}</span>
      </div>
      {trades.length === 0 ? (
        <div className="empty">No completed trades yet — the bot records every round-trip here.</div>
      ) : (
        <>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Dir</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry</th>
                  <th className="num">Exit</th>
                  <th className="num">P&amp;L</th>
                  <th>Opened</th>
                  <th>Closed</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={`${t.symbol}-${t.closed_at}-${i}`}>
                    <td>{t.symbol}</td>
                    <td><span className={`tag ${t.direction}`}>{String(t.direction).toUpperCase()}</span></td>
                    <td className="num">{t.qty}</td>
                    <td className="num">{t.entry_price.toFixed(2)}</td>
                    <td className="num">{t.exit_price.toFixed(2)}</td>
                    <td className={`num ${t.pnl >= 0 ? "green" : "red"}`}>
                      {fmt$(t.pnl)}
                      <span className="dim" style={{ marginLeft: 6 }}>{(t.pnl_pct * 100).toFixed(2)}%</span>
                    </td>
                    <td className="dim">{fmtTs(t.opened_at)}</td>
                    <td className="dim">{fmtTs(t.closed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card-b" style={{ display: "flex", gap: 20, borderTop: "1px solid var(--border)" }}>
            <span className="dim mono" style={{ fontSize: 12 }}>
              Win rate <span style={{ color: "var(--text)" }}>{winRate.toFixed(1)}%</span>
            </span>
            <span className="dim mono" style={{ fontSize: 12 }}>
              Total P&amp;L <span className={totalPnl >= 0 ? "green" : "red"}>{fmt$(totalPnl)}</span>
            </span>
            <span className="dim mono" style={{ fontSize: 12 }}>
              Profit factor{" "}
              <span style={{ color: "var(--text)" }}>
                {profitFactor === Infinity ? "∞" : profitFactor.toFixed(2)}
              </span>
            </span>
          </div>
        </>
      )}
    </div>
  );
}
