import { PredictionResponse } from "../api";

interface Props {
  prediction: PredictionResponse | null;
}

const SPARK = "▁▂▃▄▅▆▇█";
function sparkline(values: number[], width = 60): string {
  if (!values.length) return "";
  const v = values.slice(-width);
  const lo = Math.min(...v, -0.05);
  const hi = Math.max(...v,  0.05);
  if (hi === lo) return SPARK[0].repeat(v.length);
  const step = SPARK.length - 1;
  return v.map((x) => SPARK[Math.round(((x - lo) / (hi - lo)) * step)]).join("");
}

export function PredictionPanel({ prediction }: Props) {
  if (!prediction) {
    return (
      <div className="card">
        <div className="card-h">Live prediction</div>
        <div className="empty">No prediction yet — choose a symbol on the chart.</div>
      </div>
    );
  }

  const dir = prediction.current_direction;
  const score = prediction.current_score;
  const colour = dir > 0 ? "var(--green)" : dir < 0 ? "var(--red)" : "var(--text-dim)";
  const label = dir > 0 ? "LONG" : dir < 0 ? "SHORT" : "FLAT";

  const expectedMove = prediction.plan
    ? Math.abs(prediction.plan.take_profit - prediction.plan.entry)
    : 0;
  const expectedPct = prediction.plan
    ? (expectedMove / prediction.plan.entry) * 100
    : 0;

  const scoreSeries = prediction.score_history.map((s) => s.score);
  const longCount = prediction.score_history.filter((s) => s.direction > 0).length;
  const shortCount = prediction.score_history.filter((s) => s.direction < 0).length;
  const flatCount = prediction.score_history.length - longCount - shortCount;

  const meterPct = Math.min(100, Math.max(0, ((score + 1) / 2) * 100));

  return (
    <div className="card">
      <div className="card-h">
        Live prediction
        <span className="dim mono right">{prediction.symbol}</span>
      </div>
      <div className="card-b">
        <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 12, alignItems: "center" }}>
          <div>
            <div className="label dim mono" style={{ fontSize: 11, marginBottom: 4 }}>SCORE METER</div>
            <div style={{ position: "relative", height: 14, background: "var(--bg-2)", borderRadius: 999, overflow: "hidden" }}>
              <div style={{
                position: "absolute", left: 0, top: 0, bottom: 0, width: "50%",
                background: "linear-gradient(90deg, #ff5d6c, #ff5d6c40)",
              }} />
              <div style={{
                position: "absolute", right: 0, top: 0, bottom: 0, width: "50%",
                background: "linear-gradient(90deg, #3bd28140, #3bd281)",
              }} />
              <div style={{
                position: "absolute", left: `${meterPct}%`, top: -2, bottom: -2, width: 2,
                background: colour, boxShadow: "0 0 0 1px #0c1014",
              }} />
            </div>
            <div className="dim mono" style={{ fontSize: 11, marginTop: 4, display: "flex", justifyContent: "space-between" }}>
              <span>−1</span><span>0</span><span>+1</span>
            </div>
          </div>

          <div style={{ textAlign: "right" }}>
            <div className="label dim mono" style={{ fontSize: 11 }}>DIRECTION</div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 28, color: colour, fontWeight: 700 }}>
              {label}
            </div>
            <div className="dim mono" style={{ fontSize: 12 }}>
              {score >= 0 ? "+" : ""}{score.toFixed(3)}
            </div>
          </div>
        </div>

        <div className="row three" style={{ marginTop: 12 }}>
          <div className="kpi">
            <div className="label">RSI</div>
            <div className="value">{prediction.rsi.toFixed(1)}</div>
          </div>
          <div className="kpi">
            <div className="label">ADX</div>
            <div className="value">{prediction.adx.toFixed(1)}</div>
          </div>
          <div className="kpi">
            <div className="label">ATR</div>
            <div className="value">{prediction.atr.toFixed(3)}</div>
          </div>
        </div>

        {prediction.plan && (
          <div style={{ marginTop: 12 }}>
            <div className="label dim mono" style={{ fontSize: 11, marginBottom: 4 }}>
              PROJECTED TRADE PLAN
            </div>
            <table>
              <tbody>
                <tr>
                  <td>Entry</td>
                  <td className="num mono">${prediction.plan.entry.toFixed(2)}</td>
                  <td className="dim">market</td>
                </tr>
                <tr>
                  <td>Stop</td>
                  <td className="num mono red">${prediction.plan.stop.toFixed(2)}</td>
                  <td className="dim">{prediction.plan.risk_per_share.toFixed(2)} risk/sh</td>
                </tr>
                <tr>
                  <td>Take profit</td>
                  <td className="num mono green">${prediction.plan.take_profit.toFixed(2)}</td>
                  <td className="dim">+{expectedPct.toFixed(2)}% target</td>
                </tr>
                <tr>
                  <td>R:R</td>
                  <td className="num mono">{prediction.plan.rr_ratio.toFixed(2)}</td>
                  <td className="dim">{prediction.current_reason}</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}

        <div style={{ marginTop: 12 }}>
          <div className="label dim mono" style={{ fontSize: 11, marginBottom: 4 }}>
            RECENT SCORE  (last {prediction.score_history.length} bars)
          </div>
          <div className="mono" style={{ fontSize: 14, letterSpacing: 1 }}>
            {sparkline(scoreSeries, 80)}
          </div>
          <div className="dim mono" style={{ fontSize: 11, marginTop: 4 }}>
            <span className="green">●</span> long {longCount}{"   "}
            <span className="red">●</span> short {shortCount}{"   "}
            <span className="dim">●</span> flat {flatCount}
          </div>
        </div>
      </div>
    </div>
  );
}
