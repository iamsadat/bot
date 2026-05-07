// Tiny typed fetch wrapper.

export type Mode = "paper" | "live";

export interface AccountInfo {
  broker: string;
  mode: Mode;
  cash: number;
  equity: number;
  buying_power: number;
  portfolio_value: number;
  day_pnl: number;
  day_pnl_pct: number;
  is_market_open: boolean;
}

export interface PositionInfo {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  market_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  side: "long" | "short";
}

export interface OrderInfo {
  id: number | null;
  ts: string;
  symbol: string;
  side: string;
  qty: number;
  type: string;
  status: string;
  broker_order_id: string | null;
  source: string;
  limit_price: number | null;
  stop_price: number | null;
  take_profit: number | null;
  rejected_reason: string | null;
}

export interface StrategyConfig {
  symbols: string[];
  entry_threshold: number;
  adx_min: number;
  risk_per_trade: number;
  rr_ratio: number;
  stop_atr_mult: number;
  auto_trade: boolean;
}

export interface StrategyState {
  running: boolean;
  mode: Mode;
  kill_switch: boolean;
  halted_today: boolean;
  halted_reason: string | null;
  last_tick: string | null;
  last_decision: any | null;
  config: StrategyConfig;
}

export interface AuditEntry {
  id: number;
  ts: string;
  kind: string;
  actor: string;
  summary: string;
  detail: any | null;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? body.detail
        : res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body as T;
}

export const api = {
  account: () => req<AccountInfo>("/api/account"),
  positions: () => req<PositionInfo[]>("/api/positions"),
  orders: () => req<OrderInfo[]>("/api/orders"),
  openBrokerOrders: () => req<any[]>("/api/orders/open"),
  bars: (symbol: string, lookback = 240) =>
    req<{ symbol: string; bars: any[] }>(
      `/api/market/bars/${symbol}?lookback_minutes=${lookback}`,
    ),

  strategy: () => req<StrategyState>("/api/strategy"),
  startStrategy: () =>
    req("/api/strategy/start", { method: "POST" }),
  stopStrategy: () =>
    req("/api/strategy/stop", { method: "POST" }),
  updateConfig: (cfg: StrategyConfig) =>
    req<StrategyConfig>("/api/strategy/config", {
      method: "PUT",
      body: JSON.stringify(cfg),
    }),

  placeOrder: (body: any) =>
    req<OrderInfo>("/api/orders", { method: "POST", body: JSON.stringify(body) }),
  cancelOrder: (broker_order_id: string) =>
    req("/api/orders/cancel", {
      method: "POST",
      body: JSON.stringify({ broker_order_id, cancel_all: false }),
    }),
  cancelAll: () =>
    req("/api/orders/cancel", {
      method: "POST",
      body: JSON.stringify({ cancel_all: true }),
    }),
  closePosition: (symbol: string) =>
    req(`/api/positions/${symbol}/close`, { method: "POST" }),

  kill: () => req("/api/safety/kill", { method: "POST" }),
  releaseKill: () => req("/api/safety/release", { method: "POST" }),
  changeMode: (mode: Mode, confirmation?: string) =>
    req("/api/safety/mode", {
      method: "POST",
      body: JSON.stringify({ mode, confirmation }),
    }),

  audit: (limit = 200) => req<AuditEntry[]>(`/api/audit?limit=${limit}`),
};
