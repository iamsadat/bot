import { AccountInfo } from "../api";

interface Props {
  account: AccountInfo | null;
  error: string | null;
}

const fmt$ = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

const fmtPct = (n: number) =>
  `${n >= 0 ? "+" : ""}${(n * 100).toFixed(2)}%`;

export function AccountPanel({ account, error }: Props) {
  return (
    <div className="card">
      <div className="card-h">Account</div>

      {error && (
        <div className="callout">
          {error.includes("not set")
            ? "Broker is not configured. Add ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET to .env and restart the API."
            : error}
        </div>
      )}

      {account && (
        <div className="card-b">
          <div className="kpi-grid">
            <div className="kpi">
              <div className="label">Equity</div>
              <div className="value">{fmt$(account.equity)}</div>
            </div>
            <div className="kpi">
              <div className="label">Cash</div>
              <div className="value">{fmt$(account.cash)}</div>
            </div>
            <div className="kpi">
              <div className="label">Buying Power</div>
              <div className="value">{fmt$(account.buying_power)}</div>
            </div>
            <div className="kpi">
              <div className="label">Day P&L</div>
              <div className={`value ${account.day_pnl >= 0 ? "green" : "red"}`}>
                {account.day_pnl >= 0 ? "+" : ""}{fmt$(account.day_pnl)}
                <span className="dim mono" style={{ fontSize: 12, marginLeft: 8 }}>
                  {fmtPct(account.day_pnl_pct)}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
      {!account && !error && <div className="empty">Loading…</div>}
    </div>
  );
}
