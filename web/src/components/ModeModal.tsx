import { useState } from "react";
import { Mode } from "../api";

interface Props {
  target: Mode;
  current: Mode;
  onCancel: () => void;
  onConfirm: (confirmation?: string) => Promise<void>;
}

export function ModeModal({ target, current, onCancel, onConfirm }: Props) {
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);

  if (target === current) {
    return (
      <div className="modal-bg" onClick={onCancel}>
        <div className="modal" onClick={(e) => e.stopPropagation()}>
          <h2>Already in {current.toUpperCase()} mode</h2>
          <button className="ghost" onClick={onCancel}>Close</button>
        </div>
      </div>
    );
  }

  const isLive = target === "live";

  return (
    <div className="modal-bg" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>
          Switch to {target.toUpperCase()}{" "}
          <span className={`badge ${target}`} style={{ marginLeft: 8 }}>{target}</span>
        </h2>
        <p className="dim" style={{ fontSize: 13 }}>
          {isLive ? (
            <>
              You are about to enable <strong style={{ color: "var(--red)" }}>real-money trading</strong>.
              Orders submitted from this UI or by the strategy engine will hit a real brokerage account
              and result in real fills, real fees, and real losses. The engine must be stopped first.
            </>
          ) : (
            <>
              Switching back to paper trading. Live orders are not affected; only future orders route through
              the paper account.
            </>
          )}
        </p>

        {isLive && (
          <>
            <div className="callout danger" style={{ margin: "0 0 12px" }}>
              Type <strong>I_UNDERSTAND_REAL_MONEY</strong> to confirm.
            </div>
            <input
              type="text"
              placeholder="I_UNDERSTAND_REAL_MONEY"
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              autoFocus
            />
          </>
        )}

        <div className="btn-row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          <button className="ghost" onClick={onCancel}>Cancel</button>
          <button
            className={isLive ? "danger" : "primary"}
            disabled={busy || (isLive && phrase !== "I_UNDERSTAND_REAL_MONEY")}
            onClick={async () => {
              setBusy(true);
              try { await onConfirm(isLive ? phrase : undefined); }
              finally { setBusy(false); }
            }}
          >
            {busy ? "Switching…" : `Switch to ${target.toUpperCase()}`}
          </button>
        </div>
      </div>
    </div>
  );
}
