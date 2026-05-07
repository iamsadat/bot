import { AuditEntry } from "../api";

interface Props { entries: AuditEntry[]; }

const COLOR: Record<string, string> = {
  order_submitted: "var(--green)",
  order_rejected: "var(--red)",
  order_error: "var(--red)",
  order_cancel: "var(--text-dim)",
  cancel_all: "var(--text-dim)",
  kill_switch: "var(--red)",
  kill_reset: "var(--accent)",
  engine_start: "var(--green)",
  engine_stop: "var(--text-dim)",
  engine_error: "var(--red)",
  rate_limited: "var(--yellow)",
  mode_changed: "var(--purple)",
  strategy_config_updated: "var(--accent)",
};

export function AuditLog({ entries }: Props) {
  return (
    <div className="card">
      <div className="card-h">
        Audit Log
        <span className="dim mono right">{entries.length}</span>
      </div>
      {entries.length === 0 ? (
        <div className="empty">No audit entries</div>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Kind</th>
                <th>Actor</th>
                <th>Summary</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td className="dim">{new Date(e.ts).toLocaleTimeString()}</td>
                  <td style={{ color: COLOR[e.kind] || "var(--text)" }}>{e.kind}</td>
                  <td className="dim">{e.actor}</td>
                  <td>{e.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
