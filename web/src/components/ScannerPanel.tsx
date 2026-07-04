import { Candidate, ScanResult } from "../api";

interface Props {
  scan: ScanResult | null;
}

const REGIME_LABEL: Record<string, { text: string; cls: string }> = {
  trend_up: { text: "TREND ↑", cls: "green" },
  trend_down: { text: "TREND ↓", cls: "red" },
  chop: { text: "CHOP", cls: "chop" },
};

function RegimeBadge({ regime }: { regime?: string }) {
  const r = regime ? REGIME_LABEL[regime] : undefined;
  if (!r) return <span className="badge dim right">—</span>;
  return <span className={`badge regime-${r.cls} right`}>{r.text}</span>;
}

function directionLabel(d?: number) {
  if (!d) return "FLAT";
  return d > 0 ? "LONG" : "SHORT";
}

export function ScannerPanel({ scan }: Props) {
  const candidates: Candidate[] = scan?.candidates ?? [];
  const sorted = [...candidates].sort(
    (a, b) => Math.abs(b.score ?? 0) - Math.abs(a.score ?? 0),
  );

  return (
    <div className="card">
      <div className="card-h">
        Market Scanner
        {scan?.stale && <span className="badge dim" style={{ marginLeft: 8 }}>LAST SESSION</span>}
        <RegimeBadge regime={scan?.regime} />
      </div>
      {sorted.length === 0 ? (
        <div className="empty">No scan yet — start the engine.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="num">Price</th>
              <th className="num">Score</th>
              <th>Dir</th>
              <th>MTF</th>
              <th className="num">News</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((c, i) => {
              const score = c.score ?? 0;
              const news = c.news_score;
              return (
                <tr key={c.symbol ?? i}>
                  <td>{c.symbol ?? "—"}</td>
                  <td className="num dim">{c.price != null ? c.price.toFixed(2) : "—"}</td>
                  <td className={`num ${score > 0 ? "green" : score < 0 ? "red" : "dim"}`}>
                    {score >= 0 ? "+" : ""}{score.toFixed(2)}
                  </td>
                  <td>
                    <span className={`tag ${c.direction && c.direction > 0 ? "long" : c.direction && c.direction < 0 ? "short" : ""}`}>
                      {directionLabel(c.direction)}
                    </span>
                  </td>
                  <td className="dim">{c.mtf === undefined ? "—" : c.mtf ? "✓" : "✗"}</td>
                  <td className="num">
                    {news === undefined || news === null ? (
                      <span className="dim">—</span>
                    ) : (
                      <>
                        <span className={`dot ${news > 0 ? "green" : news < 0 ? "red" : "dim"}`} />
                        <span className={news > 0 ? "green" : news < 0 ? "red" : "dim"}>
                          {news.toFixed(2)}
                        </span>
                        {news <= -0.4 && <span className="red" title="Negative news"> ⚠</span>}
                      </>
                    )}
                  </td>
                  <td>{c.selected && <span className="tag traded">TRADED</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
