import { useEffect, useState } from "react";
import { Mode, api } from "../api";

interface Props {
  initialMode?: Mode;
  onClose: () => void;
  onSaved: () => void;
}

export function SetupModal({ initialMode = "paper", onClose, onSaved }: Props) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tested, setTested] = useState<{ equity: number; cash: number; buying_power: number } | null>(null);
  const [status, setStatus] = useState<Record<Mode, { configured: boolean; source: string; key_preview: string | null }> | null>(null);

  useEffect(() => {
    api.setupStatus().then(setStatus).catch(() => {});
  }, []);

  const test = async () => {
    setErr(null); setBusy(true); setTested(null);
    try {
      const r = await api.setupTest(mode, key, secret);
      setTested({ equity: r.equity, cash: r.cash, buying_power: r.buying_power });
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setErr(null); setBusy(true);
    try {
      await api.setupSave(mode, key, secret);
      onSaved();
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    if (!confirm(`Clear ${mode} keys? (This will not affect environment variables.)`)) return;
    setErr(null); setBusy(true);
    try {
      await api.setupClear(mode);
      const s = await api.setupStatus();
      setStatus(s);
      onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const cur = status?.[mode];

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: "min(560px, 96vw)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h2 style={{ margin: 0, flex: 1 }}>Connect Alpaca</h2>
          <button className="ghost" onClick={onClose}>✕</button>
        </div>

        <div className="btn-row" style={{ marginTop: 8 }}>
          <button
            className={mode === "paper" ? "primary" : "ghost"}
            onClick={() => { setMode("paper"); setTested(null); setErr(null); }}
          >Paper</button>
          <button
            className={mode === "live" ? "danger" : "ghost"}
            onClick={() => { setMode("live"); setTested(null); setErr(null); }}
          >Live</button>
          <span className="dim mono" style={{ alignSelf: "center", fontSize: 11, marginLeft: "auto" }}>
            {cur?.configured
              ? `currently set via ${cur.source} (${cur.key_preview ?? "—"})`
              : "not configured"}
          </span>
        </div>

        <div className="callout" style={{ marginTop: 12, fontSize: 12 }}>
          {mode === "paper" ? (
            <>
              Paper-trading keys. Get them at{" "}
              <span className="mono">app.alpaca.markets/paper/dashboard/overview</span>{" "}
              → <strong>View API Keys</strong>.
              No real money is involved.
            </>
          ) : (
            <>
              <span style={{ color: "var(--red)" }}>Live keys.</span>{" "}
              Storing these makes real trading possible. Switching <em>into</em> live mode still
              requires the explicit confirmation phrase.
            </>
          )}
        </div>

        <div className="field" style={{ marginTop: 8 }}>
          <label>API Key</label>
          <input type="text" value={key} onChange={(e) => setKey(e.target.value.trim())}
                 placeholder={mode === "paper" ? "PK..." : "AK..."} autoFocus />
        </div>
        <div className="field">
          <label>API Secret</label>
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value.trim())}
                 placeholder="••••••••••••••••••••••••" />
        </div>

        {err && <div className="callout danger" style={{ margin: "0 0 8px" }}>{err}</div>}

        {tested && (
          <div className="callout" style={{
            margin: "8px 0 0", background: "#0e2a17",
            borderColor: "#1c6e3a", color: "#9bf2bd",
          }}>
            ✓ Connection OK. Equity ${tested.equity.toLocaleString()}, BP ${tested.buying_power.toLocaleString()}.
          </div>
        )}

        <div className="btn-row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          {cur?.configured && cur.source === "db" && (
            <button className="ghost" onClick={clear} disabled={busy}>Clear stored {mode}</button>
          )}
          <span style={{ flex: 1 }} />
          <button className="ghost" onClick={test} disabled={busy || !key || !secret}>
            {busy ? "Testing…" : "Test"}
          </button>
          <button
            className="primary"
            onClick={save}
            disabled={busy || !key || !secret}
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
