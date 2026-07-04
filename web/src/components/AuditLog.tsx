import { Fragment, useState } from "react";
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
  const [expanded, setExpanded] = useState<number | null>(null);
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
              {entries.map((e) => {
                const hasDetail = e.detail != null;
                const isOpen = expanded === e.id;
                return (
                  <Fragment key={e.id}>
                    <tr
                      onClick={() => hasDetail && setExpanded(isOpen ? null : e.id)}
                      style={hasDetail ? { cursor: "pointer" } : undefined}
                    >
                      <td className="dim">{new Date(e.ts).toLocaleTimeString()}</td>
                      <td style={{ color: COLOR[e.kind] || "var(--text)" }}>{e.kind}</td>
                      <td className="dim">{e.actor}</td>
                      <td>{e.summary}{hasDetail && (isOpen ? " ▾" : " ▸")}</td>
                    </tr>
                    {isOpen && hasDetail && (
                      <tr>
                        <td colSpan={4} style={{ background: "var(--bg-2)" }}>
                          <pre className="mono" style={{ margin: 0, fontSize: 11, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                            {JSON.stringify(e.detail, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
