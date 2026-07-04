import { useEffect, useRef, useState } from "react";
import { AreaData, ColorType, IChartApi, ISeriesApi, UTCTimestamp, createChart } from "lightweight-charts";
import { AccountInfo, api } from "../api";

interface Props {
  account: AccountInfo | null;
  error: string | null;
}

const fmt$ = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

const fmtPct = (n: number) =>
  `${n >= 0 ? "+" : ""}${(n * 100).toFixed(2)}%`;

type Period = "1D" | "1W" | "1M";

function EquityChart() {
  const [period, setPeriod] = useState<Period>("1D");
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const areaRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 180,
      layout: { background: { type: ColorType.Solid, color: "#131820" }, textColor: "#8b97a8" },
      grid: { vertLines: { color: "#1f2733" }, horzLines: { color: "#1f2733" } },
      timeScale: { borderColor: "#2a3340", timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: "#2a3340" },
    });
    chartRef.current = chart;
    areaRef.current = chart.addAreaSeries({
      lineColor: "#4f8cff", topColor: "#4f8cff40", bottomColor: "#4f8cff00", lineWidth: 2,
    });
    const onResize = () => containerRef.current && chart.applyOptions({ width: containerRef.current.clientWidth });
    const ro = new ResizeObserver(onResize);
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.accountHistory(period).then((h) => {
      if (cancelled || !areaRef.current) return;
      const data: AreaData[] = h.timestamps.map((t, i) => ({
        time: t as UTCTimestamp,
        value: h.equity[i],
      }));
      areaRef.current.setData(data);
      chartRef.current?.timeScale().fitContent();
    }, () => { /* history endpoint not shipped yet — leave chart empty */ });
    return () => { cancelled = true; };
  }, [period]);

  return (
    <div style={{ marginTop: 12 }}>
      <div className="btn-row" style={{ marginBottom: 6 }}>
        {(["1D", "1W", "1M"] as Period[]).map((p) => (
          <button
            key={p}
            className={period === p ? "primary" : "ghost"}
            style={{ padding: "2px 8px", fontSize: 11 }}
            onClick={() => setPeriod(p)}
          >{p}</button>
        ))}
      </div>
      <div ref={containerRef} />
    </div>
  );
}

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
          <EquityChart />
        </div>
      )}
      {!account && !error && <div className="empty">Loading…</div>}
    </div>
  );
}
