import { PositionInfo } from "../api";

interface Props {
  positions: PositionInfo[];
  onClose: (symbol: string) => void;
}

const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

export function PositionsPanel({ positions, onClose }: Props) {
  return (
    <div className="card">
      <div className="card-h">
        Positions <span className="dim mono right">{positions.length}</span>
      </div>
      {positions.length === 0 ? (
        <div className="empty">No open positions</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th className="num">Qty</th>
              <th className="num">Avg</th>
              <th className="num">Mark</th>
              <th className="num">P&amp;L</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.symbol}>
                <td>{p.symbol}</td>
                <td><span className={`tag ${p.side}`}>{p.side.toUpperCase()}</span></td>
                <td className="num">{fmt(p.qty)}</td>
                <td className="num">{fmt(p.avg_entry_price)}</td>
                <td className="num">{fmt(p.market_price)}</td>
                <td className={`num ${p.unrealized_pnl >= 0 ? "green" : "red"}`}>
                  {p.unrealized_pnl >= 0 ? "+" : ""}{fmt(p.unrealized_pnl)}
                  <span className="dim" style={{ marginLeft: 6 }}>
                    {(p.unrealized_pnl_pct * 100).toFixed(2)}%
                  </span>
                </td>
                <td>
                  <button className="ghost" onClick={() => onClose(p.symbol)}>Close</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
