import { useState } from "react";
import { AccountInfo } from "../api";

interface Props {
  account: AccountInfo | null;
  onSubmit: (req: any) => Promise<void>;
}

export function OrderTicket({ account, onSubmit }: Props) {
  const [symbol, setSymbol] = useState("SPY");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState<number>(1);
  const [type, setType] = useState<"market" | "limit" | "bracket">("market");
  const [limit, setLimit] = useState<string>("");
  const [stop, setStop] = useState<string>("");
  const [tp, setTp] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    setBusy(true);
    try {
      const body: any = {
        symbol: symbol.toUpperCase(),
        side, qty, type,
      };
      if (type === "limit") body.limit_price = Number(limit);
      if (type === "bracket") {
        body.stop_loss = Number(stop);
        body.take_profit = Number(tp);
      }
      await onSubmit(body);
      setQty(1);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const disabled = !account || busy || !symbol || qty <= 0
    || (type === "limit" && !limit)
    || (type === "bracket" && (!stop || !tp));

  return (
    <div className="card">
      <div className="card-h">Manual Order</div>
      <div className="card-b">
        <div className="row">
          <div className="field">
            <label>Symbol</label>
            <input type="text" value={symbol}
                   onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
          </div>
          <div className="field">
            <label>Side</label>
            <select value={side} onChange={(e) => setSide(e.target.value as any)}>
              <option value="buy">BUY</option>
              <option value="sell">SELL</option>
            </select>
          </div>
        </div>

        <div className="row">
          <div className="field">
            <label>Quantity</label>
            <input type="number" min={1} step={1} value={qty}
                   onChange={(e) => setQty(Number(e.target.value))} />
          </div>
          <div className="field">
            <label>Type</label>
            <select value={type} onChange={(e) => setType(e.target.value as any)}>
              <option value="market">Market</option>
              <option value="limit">Limit</option>
              <option value="bracket">Bracket (Mkt + SL + TP)</option>
            </select>
          </div>
        </div>

        {type === "limit" && (
          <div className="field">
            <label>Limit price</label>
            <input type="number" step="0.01" value={limit}
                   onChange={(e) => setLimit(e.target.value)} />
          </div>
        )}

        {type === "bracket" && (
          <div className="row">
            <div className="field">
              <label>Stop loss</label>
              <input type="number" step="0.01" value={stop}
                     onChange={(e) => setStop(e.target.value)} />
            </div>
            <div className="field">
              <label>Take profit</label>
              <input type="number" step="0.01" value={tp}
                     onChange={(e) => setTp(e.target.value)} />
            </div>
          </div>
        )}

        {err && <div className="callout danger">{err}</div>}

        <div className="btn-row" style={{ marginTop: 8 }}>
          <button
            className={side === "buy" ? "success" : "danger"}
            disabled={disabled}
            onClick={submit}
          >
            {busy ? "Submitting…" : `${side === "buy" ? "Buy" : "Sell"} ${qty} ${symbol}`}
          </button>
          <span className="dim mono" style={{ alignSelf: "center", marginLeft: "auto" }}>
            {account ? `Buying power $${account.buying_power.toLocaleString()}` : "—"}
          </span>
        </div>
      </div>
    </div>
  );
}
