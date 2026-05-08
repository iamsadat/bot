import { useEffect, useRef, useState } from "react";
import {
  CandlestickData,
  ColorType,
  IChartApi,
  ISeriesApi,
  LineData,
  LineStyle,
  PriceScaleMode,
  Time,
  UTCTimestamp,
  createChart,
} from "lightweight-charts";
import { IndicatorBar, PredictionResponse, TradePlan } from "../api";

interface Props {
  data: PredictionResponse | null;
  onPickSymbol?: (s: string) => void;
  symbol: string;
  setSymbol: (s: string) => void;
  busy: boolean;
}

const toTime = (ts: string): UTCTimestamp =>
  Math.floor(new Date(ts).getTime() / 1000) as UTCTimestamp;

type IndicatorKey = "ema9" | "ema21" | "ema55" | "vwap" | "bb";
const INDICATOR_LABELS: Record<IndicatorKey, string> = {
  ema9: "EMA 9",
  ema21: "EMA 21",
  ema55: "EMA 55",
  vwap: "VWAP",
  bb: "Bollinger",
};

const INDICATOR_COLORS = {
  ema9: "#4f8cff",
  ema21: "#a980ff",
  ema55: "#f0b429",
  vwap: "#3bd281",
  bbUp: "#ff5d6c80",
  bbLo: "#ff5d6c80",
  bbMid: "#ff5d6c40",
};

export function Chart({ data, symbol, setSymbol, busy }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const subRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const subChartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const ema9Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ema21Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ema55Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const vwapRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bbUpRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bbLoRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bbMidRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdSigRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdHistRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const planLines = useRef<{ stop?: any; tp?: any; entry?: any }>({});

  const [enabled, setEnabled] = useState<Record<IndicatorKey, boolean>>({
    ema9: true, ema21: true, ema55: true, vwap: true, bb: false,
  });
  const [subPlot, setSubPlot] = useState<"rsi" | "macd">("rsi");
  const [tooltip, setTooltip] = useState<{
    x: number; y: number; bar: IndicatorBar | null;
  }>({ x: 0, y: 0, bar: null });
  const [symbolDraft, setSymbolDraft] = useState(symbol);

  useEffect(() => setSymbolDraft(symbol), [symbol]);

  // ---------- chart init ----------------------------------------------
  useEffect(() => {
    if (!containerRef.current || !subRef.current) return;
    const common = {
      layout: {
        background: { type: ColorType.Solid, color: "#131820" },
        textColor: "#8b97a8",
        fontFamily: "ui-monospace, JetBrains Mono, Menlo, Consolas, monospace",
      },
      grid: {
        vertLines: { color: "#1f2733" },
        horzLines: { color: "#1f2733" },
      },
      timeScale: { borderColor: "#2a3340", timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: "#2a3340", mode: PriceScaleMode.Normal },
      crosshair: {
        vertLine: { color: "#4f8cff", style: LineStyle.Dashed, labelBackgroundColor: "#0c1014" },
        horzLine: { color: "#4f8cff", style: LineStyle.Dashed, labelBackgroundColor: "#0c1014" },
      },
    };

    const chart = createChart(containerRef.current, {
      ...common,
      width: containerRef.current.clientWidth,
      height: 360,
    });
    chartRef.current = chart;

    const subChart = createChart(subRef.current, {
      ...common,
      width: subRef.current.clientWidth,
      height: 140,
    });
    subChartRef.current = subChart;

    candleRef.current = chart.addCandlestickSeries({
      upColor: "#3bd281", downColor: "#ff5d6c",
      borderUpColor: "#3bd281", borderDownColor: "#ff5d6c",
      wickUpColor: "#3bd281", wickDownColor: "#ff5d6c",
    });
    ema9Ref.current = chart.addLineSeries({ color: INDICATOR_COLORS.ema9, lineWidth: 1, priceLineVisible: false });
    ema21Ref.current = chart.addLineSeries({ color: INDICATOR_COLORS.ema21, lineWidth: 1, priceLineVisible: false });
    ema55Ref.current = chart.addLineSeries({ color: INDICATOR_COLORS.ema55, lineWidth: 1, priceLineVisible: false });
    vwapRef.current = chart.addLineSeries({ color: INDICATOR_COLORS.vwap, lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false });
    bbUpRef.current = chart.addLineSeries({ color: INDICATOR_COLORS.bbUp, lineWidth: 1, priceLineVisible: false });
    bbLoRef.current = chart.addLineSeries({ color: INDICATOR_COLORS.bbLo, lineWidth: 1, priceLineVisible: false });
    bbMidRef.current = chart.addLineSeries({ color: INDICATOR_COLORS.bbMid, lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false });

    rsiRef.current = subChart.addLineSeries({ color: "#a980ff", lineWidth: 1 });
    macdRef.current = subChart.addLineSeries({ color: "#4f8cff", lineWidth: 1 });
    macdSigRef.current = subChart.addLineSeries({ color: "#f0b429", lineWidth: 1 });
    macdHistRef.current = subChart.addHistogramSeries({ color: "#3bd281" });

    // Sync timescales
    const main = chart.timeScale();
    const sub = subChart.timeScale();
    main.subscribeVisibleLogicalRangeChange((r) => r && sub.setVisibleLogicalRange(r));
    sub.subscribeVisibleLogicalRangeChange((r) => r && main.setVisibleLogicalRange(r));

    chart.subscribeCrosshairMove((p) => {
      if (!p.time || !p.point) {
        setTooltip((t) => ({ ...t, bar: null }));
        return;
      }
      // Will be filled in by data effect below.
      setTooltip({
        x: p.point.x,
        y: p.point.y,
        bar: lookupRef.current[p.time as number] ?? null,
      });
    });

    const onResize = () => {
      if (containerRef.current && chartRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
      if (subRef.current && subChartRef.current) {
        subChart.applyOptions({ width: subRef.current.clientWidth });
      }
    };
    const ro = new ResizeObserver(onResize);
    if (containerRef.current) ro.observe(containerRef.current);
    if (subRef.current) ro.observe(subRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      subChart.remove();
      chartRef.current = null;
      subChartRef.current = null;
    };
  }, []);

  // ---------- bars → series -------------------------------------------
  const lookupRef = useRef<Record<number, IndicatorBar>>({});

  useEffect(() => {
    if (!data || !candleRef.current) return;
    const lookup: Record<number, IndicatorBar> = {};

    const candles: CandlestickData[] = [];
    const ema9: LineData[] = [];
    const ema21: LineData[] = [];
    const ema55: LineData[] = [];
    const vwap: LineData[] = [];
    const bbUp: LineData[] = [];
    const bbLo: LineData[] = [];
    const bbMid: LineData[] = [];
    const rsi: LineData[] = [];
    const macd: LineData[] = [];
    const macdSig: LineData[] = [];
    const macdHist: { time: Time; value: number; color: string }[] = [];

    let lastTime = -1;
    for (const b of data.bars) {
      const t = toTime(b.ts);
      if (t <= lastTime) continue; // dedupe / monotonic
      lastTime = t;
      lookup[t as number] = b;
      candles.push({ time: t, open: b.open, high: b.high, low: b.low, close: b.close });
      if (b.ema_fast != null) ema9.push({ time: t, value: b.ema_fast });
      if (b.ema_mid  != null) ema21.push({ time: t, value: b.ema_mid });
      if (b.ema_slow != null) ema55.push({ time: t, value: b.ema_slow });
      if (b.vwap     != null) vwap.push({ time: t, value: b.vwap });
      if (b.bb_up    != null) bbUp.push({ time: t, value: b.bb_up });
      if (b.bb_lo    != null) bbLo.push({ time: t, value: b.bb_lo });
      if (b.bb_mid   != null) bbMid.push({ time: t, value: b.bb_mid });
      if (b.rsi      != null) rsi.push({ time: t, value: b.rsi });
      if (b.macd     != null) macd.push({ time: t, value: b.macd });
      if (b.macd_signal != null) macdSig.push({ time: t, value: b.macd_signal });
      if (b.macd_hist != null) {
        macdHist.push({ time: t, value: b.macd_hist,
                        color: b.macd_hist >= 0 ? "#3bd28160" : "#ff5d6c60" });
      }
    }
    lookupRef.current = lookup;

    candleRef.current.setData(candles);
    ema9Ref.current?.setData(enabled.ema9 ? ema9 : []);
    ema21Ref.current?.setData(enabled.ema21 ? ema21 : []);
    ema55Ref.current?.setData(enabled.ema55 ? ema55 : []);
    vwapRef.current?.setData(enabled.vwap ? vwap : []);
    bbUpRef.current?.setData(enabled.bb ? bbUp : []);
    bbLoRef.current?.setData(enabled.bb ? bbLo : []);
    bbMidRef.current?.setData(enabled.bb ? bbMid : []);

    // Subplot
    if (subPlot === "rsi") {
      rsiRef.current?.setData(rsi);
      macdRef.current?.setData([]);
      macdSigRef.current?.setData([]);
      macdHistRef.current?.setData([]);
    } else {
      rsiRef.current?.setData([]);
      macdRef.current?.setData(macd);
      macdSigRef.current?.setData(macdSig);
      macdHistRef.current?.setData(macdHist as any);
    }

    // Plan lines on the price chart
    drawPlanLines(candleRef.current, planLines.current, data.plan);
  }, [data, enabled, subPlot]);

  const last = data?.bars[data.bars.length - 1] ?? null;

  return (
    <div className="card">
      <div className="card-h">
        Chart
        <span className="dim mono" style={{ marginLeft: 6 }}>
          {data?.symbol ?? symbol}
        </span>
        <div className="right" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="text"
            value={symbolDraft}
            onChange={(e) => setSymbolDraft(e.target.value.toUpperCase())}
            onKeyDown={(e) => { if (e.key === "Enter") setSymbol(symbolDraft); }}
            style={{ width: 80 }}
          />
          <button className="ghost" onClick={() => setSymbol(symbolDraft)} disabled={busy}>
            {busy ? "Loading…" : "Load"}
          </button>
        </div>
      </div>
      <div className="card-b" style={{ paddingTop: 4 }}>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          {(Object.keys(INDICATOR_LABELS) as IndicatorKey[]).map((k) => (
            <button
              key={k}
              className={enabled[k] ? "primary" : "ghost"}
              style={{ padding: "2px 8px", fontSize: 11 }}
              onClick={() => setEnabled((s) => ({ ...s, [k]: !s[k] }))}
            >
              {INDICATOR_LABELS[k]}
            </button>
          ))}
          <span style={{ flex: 1 }} />
          <span className="dim mono" style={{ fontSize: 11 }}>SUB:</span>
          <button
            className={subPlot === "rsi" ? "primary" : "ghost"}
            style={{ padding: "2px 8px", fontSize: 11 }}
            onClick={() => setSubPlot("rsi")}
          >RSI</button>
          <button
            className={subPlot === "macd" ? "primary" : "ghost"}
            style={{ padding: "2px 8px", fontSize: 11 }}
            onClick={() => setSubPlot("macd")}
          >MACD</button>
        </div>

        <div style={{ position: "relative" }}>
          <div ref={containerRef} style={{ marginTop: 8 }} />
          {tooltip.bar && (
            <div className="chart-tip" style={{
              position: "absolute", left: Math.min(tooltip.x + 16, 280),
              top: 8, pointerEvents: "none",
              background: "#0c1014ee", border: "1px solid #2a3340", borderRadius: 6,
              padding: "6px 8px", fontFamily: "var(--mono)", fontSize: 11,
              minWidth: 200,
            }}>
              <div className="dim">{new Date(tooltip.bar.ts).toLocaleString()}</div>
              <div>O <span className="mono">{tooltip.bar.open.toFixed(2)}</span>{"  "}
                   H <span className="mono">{tooltip.bar.high.toFixed(2)}</span>{"  "}
                   L <span className="mono">{tooltip.bar.low.toFixed(2)}</span>{"  "}
                   C <span className="mono">{tooltip.bar.close.toFixed(2)}</span></div>
              <div className="dim">V {Math.round(tooltip.bar.volume).toLocaleString()}</div>
              {tooltip.bar.vwap != null && <div>VWAP {tooltip.bar.vwap.toFixed(2)}</div>}
              {tooltip.bar.rsi  != null && <div>RSI {tooltip.bar.rsi.toFixed(1)}</div>}
              {tooltip.bar.adx  != null && <div>ADX {tooltip.bar.adx.toFixed(1)}</div>}
              {tooltip.bar.atr  != null && <div>ATR {tooltip.bar.atr.toFixed(3)}</div>}
            </div>
          )}
        </div>
        <div ref={subRef} style={{ marginTop: 4 }} />

        {last && (
          <div className="dim mono" style={{ fontSize: 11, marginTop: 6, display: "flex", gap: 12, flexWrap: "wrap" }}>
            <span>Last: <span style={{ color: "var(--text)" }}>{last.close.toFixed(2)}</span></span>
            {data?.plan && (
              <>
                <span>SL <span className="red">{data.plan.stop.toFixed(2)}</span></span>
                <span>TP <span className="green">{data.plan.take_profit.toFixed(2)}</span></span>
                <span>R:R {data.plan.rr_ratio.toFixed(1)}</span>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function drawPlanLines(
  series: ISeriesApi<"Candlestick">,
  cache: { stop?: any; tp?: any; entry?: any },
  plan: TradePlan | null,
) {
  for (const k of ["stop", "tp", "entry"] as const) {
    if (cache[k]) {
      try { series.removePriceLine(cache[k]); } catch { /* ignore */ }
      cache[k] = undefined;
    }
  }
  if (!plan) return;
  cache.entry = series.createPriceLine({
    price: plan.entry, color: "#4f8cff", lineStyle: LineStyle.Dotted,
    lineWidth: 1, axisLabelVisible: true,
    title: plan.direction > 0 ? "Long entry" : "Short entry",
  });
  cache.stop = series.createPriceLine({
    price: plan.stop, color: "#ff5d6c", lineStyle: LineStyle.Dashed,
    lineWidth: 1, axisLabelVisible: true, title: "Stop",
  });
  cache.tp = series.createPriceLine({
    price: plan.take_profit, color: "#3bd281", lineStyle: LineStyle.Dashed,
    lineWidth: 1, axisLabelVisible: true, title: "Take profit",
  });
}
