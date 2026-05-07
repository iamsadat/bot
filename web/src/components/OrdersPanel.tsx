import { OrderInfo } from "../api";

interface Props {
  orders: OrderInfo[];
  onCancel: (broker_order_id: string) => void;
  onCancelAll: () => void;
}

const fmtTs = (ts: string) => {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

export function OrdersPanel({ orders, onCancel, onCancelAll }: Props) {
  const recent = orders.slice(0, 30);
  return (
    <div className="card">
      <div className="card-h">
        Orders
        <span className="dim mono">{orders.length}</span>
        <div className="right">
          <button className="ghost" onClick={onCancelAll}>Cancel all open</button>
        </div>
      </div>
      {recent.length === 0 ? (
        <div className="empty">No orders yet</div>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Side</th>
                <th className="num">Qty</th>
                <th>Type</th>
                <th>Status</th>
                <th>Source</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {recent.map((o) => (
                <tr key={o.id ?? o.broker_order_id ?? Math.random()}>
                  <td className="dim">{fmtTs(o.ts)}</td>
                  <td>{o.symbol}</td>
                  <td><span className={`tag ${o.side}`}>{o.side.toUpperCase()}</span></td>
                  <td className="num">{o.qty}</td>
                  <td>{o.type}</td>
                  <td><span className={`tag ${o.status}`}>{o.status}</span></td>
                  <td><span className={`tag ${o.source}`}>{o.source}</span></td>
                  <td>
                    {o.broker_order_id && (o.status === "submitted" || o.status === "accepted" || o.status === "new") ? (
                      <button className="ghost" onClick={() => onCancel(o.broker_order_id!)}>Cancel</button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
