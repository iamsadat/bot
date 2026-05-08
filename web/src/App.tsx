import { useCallback, useEffect, useRef, useState } from "react";
import {
  AccountInfo,
  AuditEntry,
  OrderInfo,
  PositionInfo,
  PredictionResponse,
  StrategyState,
  api,
} from "./api";
import { connectWs } from "./ws";
import { Header } from "./components/Header";
import { AccountPanel } from "./components/AccountPanel";
import { PositionsPanel } from "./components/PositionsPanel";
import { OrdersPanel } from "./components/OrdersPanel";
import { OrderTicket } from "./components/OrderTicket";
import { StrategyPanel } from "./components/StrategyPanel";
import { AuditLog } from "./components/AuditLog";
import { ModeModal } from "./components/ModeModal";
import { Chart } from "./components/Chart";
import { PredictionPanel } from "./components/PredictionPanel";
import { SetupModal } from "./components/SetupModal";

export default function App() {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [accountErr, setAccountErr] = useState<string | null>(null);
  const [positions, setPositions] = useState<PositionInfo[]>([]);
  const [orders, setOrders] = useState<OrderInfo[]>([]);
  const [strategy, setStrategy] = useState<StrategyState | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const [modeModal, setModeModal] = useState<"paper" | "live" | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);

  // Chart / prediction state
  const [symbol, setSymbol] = useState("SPY");
  const [prediction, setPrediction] = useState<PredictionResponse | null>(null);
  const [predBusy, setPredBusy] = useState(false);
  const [streamSubs, setStreamSubs] = useState<string[]>([]);
  const [livePrice, setLivePrice] = useState<number | null>(null);

  const showToast = useCallback((s: string) => {
    setToast(s);
    window.setTimeout(() => setToast(null), 3500);
  }, []);

  // ---------- polling refresh -----------------------------------------
  const refreshAll = useCallback(async () => {
    await Promise.allSettled([
      api.account().then(
        (a) => { setAccount(a); setAccountErr(null); },
        (e) => { setAccountErr(String(e)); setAccount(null); },
      ),
      api.positions().then(setPositions, () => {}),
      api.orders().then(setOrders, () => {}),
      api.strategy().then(setStrategy, () => {}),
      api.audit(50).then(setAudit, () => {}),
    ]);
  }, []);

  useEffect(() => {
    refreshAll();
    const t = window.setInterval(refreshAll, 5000);
    return () => window.clearInterval(t);
  }, [refreshAll]);

  // ---------- prediction & live subscribe ------------------------------
  const refreshPrediction = useCallback(async () => {
    if (!symbol) return;
    setPredBusy(true);
    try {
      const p = await api.prediction(symbol, 240);
      setPrediction(p);
      setLivePrice(p.last_close);
    } catch (e) {
      // 503 if broker not configured — keep silent
      if (!String(e).includes("not set")) showToast(String(e));
    } finally {
      setPredBusy(false);
    }
  }, [symbol, showToast]);

  useEffect(() => {
    refreshPrediction();
    const t = window.setInterval(refreshPrediction, 30000);
    return () => window.clearInterval(t);
  }, [refreshPrediction]);

  // Try to subscribe to live data for the chart's symbol.
  useEffect(() => {
    if (!symbol || !account) return;
    api.subscribe([symbol], account.mode).then(
      (r) => setStreamSubs(r.subscribed),
      () => {},
    );
  }, [symbol, account]);

  // ---------- websocket -----------------------------------------------
  const wsRef = useRef<() => void>();
  useEffect(() => {
    wsRef.current = connectWs(({ kind, payload }) => {
      if (kind === "decision") {
        setStrategy((s) => (s ? { ...s, last_decision: payload, last_tick: payload.ts } : s));
      }
      if (kind === "order" || kind === "kill") {
        refreshAll();
        showToast(kind === "kill" ? "Kill switch engaged"
                                  : `Order: ${payload.side} ${payload.qty} ${payload.symbol}`);
      }
      if (kind === "trade" && payload.symbol === symbol) {
        setLivePrice(payload.price);
      }
      if (kind === "bar" && payload.symbol === symbol) {
        // Live bar — refresh prediction; cheap because bars arrive at ~1/min.
        refreshPrediction();
      }
      if (kind === "stream_status") {
        setStreamSubs(payload.subscribed || []);
      }
    });
    return () => wsRef.current?.();
  }, [refreshAll, refreshPrediction, showToast, symbol]);

  // ---------- safety actions ------------------------------------------
  const handleKill = async () => {
    if (!confirm("KILL SWITCH:\n\nThis will cancel ALL open orders, flatten ALL positions, and halt the engine. Continue?")) return;
    try { await api.kill(); showToast("Kill switch engaged"); }
    catch (e) { showToast(String(e)); }
    refreshAll();
  };
  const handleRelease = async () => {
    try { await api.releaseKill(); showToast("Kill switch released"); }
    catch (e) { showToast(String(e)); }
    refreshAll();
  };

  return (
    <div className="app">
      <Header
        account={account}
        strategy={strategy}
        onKill={handleKill}
        onRelease={handleRelease}
        onSwitchMode={(m) => setModeModal(m)}
        onOpenSetup={() => setSetupOpen(true)}
        livePrice={livePrice}
        chartSymbol={symbol}
        streamSubs={streamSubs}
      />

      <div className="layout">
        <div className="col">
          <AccountPanel account={account} error={accountErr} />
          <Chart
            data={prediction}
            symbol={symbol}
            setSymbol={setSymbol}
            busy={predBusy}
          />
          <PositionsPanel
            positions={positions}
            onClose={async (s) => {
              try { await api.closePosition(s); showToast(`Closing ${s}`); }
              catch (e) { showToast(String(e)); }
              refreshAll();
            }}
          />
          <OrdersPanel
            orders={orders}
            onCancel={async (id) => {
              try { await api.cancelOrder(id); showToast("Cancelled"); }
              catch (e) { showToast(String(e)); }
              refreshAll();
            }}
            onCancelAll={async () => {
              if (!confirm("Cancel all open orders?")) return;
              try { await api.cancelAll(); showToast("Cancelled all"); }
              catch (e) { showToast(String(e)); }
              refreshAll();
            }}
          />
        </div>

        <div className="col">
          <PredictionPanel prediction={prediction} />
          <StrategyPanel
            strategy={strategy}
            onStart={async () => {
              try { await api.startStrategy(); showToast("Strategy armed"); }
              catch (e) { showToast(String(e)); }
              refreshAll();
            }}
            onStop={async () => {
              try { await api.stopStrategy(); showToast("Strategy stopped"); }
              catch (e) { showToast(String(e)); }
              refreshAll();
            }}
            onSave={async (cfg) => {
              try { await api.updateConfig(cfg); showToast("Config saved"); }
              catch (e) { showToast(String(e)); }
              refreshAll();
            }}
          />
          <OrderTicket
            account={account}
            onSubmit={async (req) => {
              await api.placeOrder(req);
              showToast("Order submitted");
              refreshAll();
            }}
          />
          <AuditLog entries={audit} />
        </div>
      </div>

      {toast && (
        <div style={{
          position: "fixed", bottom: 24, right: 24, padding: "10px 14px",
          background: "#1a2230", border: "1px solid #2a3340", borderRadius: 8,
          fontFamily: "var(--mono)", fontSize: 12, zIndex: 50,
        }}>{toast}</div>
      )}

      {modeModal && (
        <ModeModal
          target={modeModal}
          current={strategy?.mode || "paper"}
          onCancel={() => setModeModal(null)}
          onConfirm={async (confirmation) => {
            try {
              await api.changeMode(modeModal, confirmation);
              showToast(`Mode changed to ${modeModal}`);
              setModeModal(null);
              refreshAll();
            } catch (e) {
              showToast(String(e));
            }
          }}
        />
      )}

      {setupOpen && (
        <SetupModal
          initialMode={strategy?.mode || "paper"}
          onClose={() => setSetupOpen(false)}
          onSaved={() => {
            showToast("Credentials saved");
            refreshAll();
            refreshPrediction();
          }}
        />
      )}
    </div>
  );
}
