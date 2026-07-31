'use client';

import { useEffect, useState } from 'react';
import Nav from '@/components/Nav';
import Rail from '@/components/Rail';
import AnimatedNumber from '@/components/AnimatedNumber';
import { Button, Card, Input, Meter, PageHead, SectionHead, Stat, Tag } from '@/components/ui';
import { api, Contact, RadarSettings } from '@/lib/api';
import { usePoll } from '@/lib/useLive';

function StatTile({ label, value, accent, pct }: { label: string; value: number; accent?: boolean; pct?: boolean }) {
  return (
    <Stat
      label={label}
      value={pct ? `${Math.round(value * 100)}%` : <AnimatedNumber value={value} />}
      accent={accent}
    />
  );
}

export default function Insights() {
  const m = usePoll(() => api.metrics(), 4000);
  const a = usePoll(() => api.analytics(), 6000);
  const radar = usePoll(() => api.radar(), 8000);
  const skills = usePoll(() => api.skillGaps(), 10000);

  // Real funnel stages — same five stages as the top stat row, scaled to a
  // bar chart. There is no "vetted"/"replied" split in the API, so the
  // funnel sticks to what /api/metrics actually reports.
  const stages: { label: string; value: number }[] = [
    { label: 'Discovered', value: m?.discovered ?? 0 },
    { label: 'Tailored', value: m?.tailored ?? 0 },
    { label: 'Applied', value: m?.applied ?? 0 },
    { label: 'Interview', value: m?.interview ?? 0 },
    { label: 'Offer', value: m?.offer ?? 0 },
  ];
  const maxStage = Math.max(1, ...stages.map((s) => s.value));

  return (
    <>
      <Rail />
      <div className="lg:pl-[220px]">
        <div className="lg:hidden"><Nav /></div>

        <main className="relative z-10 mx-auto min-h-screen max-w-6xl">
          <div className="space-y-4 px-6 pb-12 pt-6">
            <PageHead
              title="Tracker"
              subtitle="What your pipeline looks like right now."
              actions={<Tag tone="neutral">All time</Tag>}
            />

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <StatTile label="Discovered" value={m?.discovered ?? 0} accent />
              <StatTile label="Tailored" value={m?.tailored ?? 0} />
              <StatTile label="Applied" value={m?.applied ?? 0} />
              <StatTile label="Interviews" value={m?.interview ?? 0} />
              <StatTile label="Offers" value={m?.offer ?? 0} accent />
            </div>

            <div className="grid gap-3 sm:grid-cols-4">
              <StatTile label="Callback rate" value={m?.callback_rate ?? 0} pct accent />
              <StatTile label="Evidence coverage" value={m?.evidence_coverage ?? 0} pct />
              <StatTile label="Day streak" value={m?.streak ?? 0} />
              <StatTile label="Weekly goal" value={m?.weekly_progress ?? 0} pct />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card elevation="sm">
                <div className="text-[11px] uppercase tracking-[0.09em] text-muted">Funnel</div>
                <div className="flex flex-col gap-2.5">
                  {stages.map((s) => (
                    <div key={s.label} className="flex items-center gap-3">
                      <span className="w-24 shrink-0 text-[12.5px]">{s.label}</span>
                      <Meter value={s.value / maxStage} className="flex-1" />
                      <span className="w-11 shrink-0 text-right text-[12.5px] font-semibold">{s.value}</span>
                    </div>
                  ))}
                </div>
              </Card>

              <Card elevation="sm">
                <div className="flex items-baseline">
                  <span className="text-[11px] uppercase tracking-[0.09em] text-muted">This week</span>
                  {!!m?.streak && (
                    <span className="ml-auto text-xs font-semibold text-good">{m.streak}-day streak</span>
                  )}
                </div>
                <Meter value={m?.weekly_progress ?? 0} />
                <div className="text-[12.5px] text-muted">
                  {m?.applied_this_week ?? 0} applied so far{m?.weekly_target ? ` · target ${m.weekly_target}/week` : ''}
                </div>
              </Card>
            </div>

            {/* A/B résumé strategy */}
            {!!a?.variants?.length && (
              <Card elevation="sm">
                <SectionHead title="Résumé strategy A/B (which converts to interviews)" />
                <table className="table">
                  <thead>
                    <tr><th>Variant</th><th>Sent</th><th>Interviews</th><th>Rate</th></tr>
                  </thead>
                  <tbody>
                    {a.variants.map((v) => (
                      <tr key={v.name}>
                        <td>
                          {v.name}
                          {a.winner === v.name && <Tag tone="accent-2" className="ml-2">winner</Tag>}
                        </td>
                        <td>{v.impressions}</td><td>{v.successes}</td>
                        <td className="font-semibold" style={{ color: 'var(--color-accent)' }}>
                          {Math.round(v.success_rate * 100)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}

            <CareerRadar radar={radar} />

            {/* Skills to grow */}
            {!!skills?.gaps?.length && (
              <Card elevation="sm">
                <SectionHead title="Skills to grow (most-missed across your matches)" />
                <ul className="space-y-2">
                  {skills.gaps.slice(0, 8).map((g) => (
                    <li key={g.skill} className="flex flex-wrap items-center gap-2 text-sm">
                      <Tag tone="accent">{g.skill}</Tag>
                      <span className="text-xs text-muted">missed in {g.count} role(s)</span>
                      {g.resources?.slice(0, 2).map((r) => (
                        <a key={r.url} href={r.url} target="_blank" rel="noreferrer"
                           className="text-xs" style={{ color: 'var(--color-accent-2-700)' }}>{r.title}</a>
                      ))}
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            <Contacts />
          </div>
        </main>
      </div>
    </>
  );
}

function CareerRadar({ radar }: { radar: any }) {
  const [s, setS] = useState<RadarSettings | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { api.radarSettings().then(setS).catch(() => {}); }, []);
  const save = async (patch: Partial<RadarSettings>) => {
    if (!s) return;
    setSaving(true); setS({ ...s, ...patch });
    try { await api.setRadar(patch); } finally { setSaving(false); }
  };
  const mv = radar?.market_value?.length ? radar.market_value[radar.market_value.length - 1] : null;
  return (
    <Card elevation="sm">
      <SectionHead title="Career Radar — passive, always-on" />
      {s && (
        <div className="grid gap-3 sm:grid-cols-2">
          <button
            onClick={() => save({ radar_enabled: !s.radar_enabled })}
            className="flex items-center justify-between rounded-xl2 p-3 text-left"
            style={{
              background: s.radar_enabled ? 'var(--color-accent-2-100)' : 'var(--color-bg)',
              border: `1px solid ${s.radar_enabled ? 'var(--color-accent-2-400)' : 'var(--color-divider)'}`,
            }}
          >
            <span className="text-sm font-medium">Radar {s.radar_enabled ? 'on' : 'off'}</span>
            <span className="text-[11px] text-muted">{saving ? 'saving…' : 'pings only for roles that beat your current comp/title'}</span>
          </button>
          <label className="text-[11px] text-muted">Current salary
            <Input type="number" defaultValue={s.current_salary ?? undefined}
              onBlur={(e) => save({ current_salary: parseInt(e.target.value || '0', 10) || null })}
              className="mt-1" />
          </label>
          <label className="text-[11px] text-muted">Current title
            <Input defaultValue={s.current_title}
              onBlur={(e) => save({ current_title: e.target.value })}
              className="mt-1" />
          </label>
          <label className="text-[11px] text-muted">Watchlist keywords (comma-sep)
            <Input defaultValue={(s.radar_keywords || []).join(', ')}
              onBlur={(e) => save({ radar_keywords: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })}
              className="mt-1" />
          </label>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-4 text-sm">
        {mv && (
          <span className="text-muted">
            Market value (median): <span className="font-semibold" style={{ color: 'var(--color-accent)' }}>{Math.round(mv.median / 1000)}k {mv.currency}</span>
          </span>
        )}
        {!!radar?.hits?.length && <span className="text-muted">{radar.hits.length} radar hit(s) waiting</span>}
      </div>
    </Card>
  );
}

function Contacts() {
  const [list, setList] = useState<Contact[]>([]);
  const [due, setDue] = useState(false);
  const [form, setForm] = useState<Partial<Contact>>({});
  const load = () => api.contacts(due).then((r) => setList(r.contacts || [])).catch(() => {});
  useEffect(() => { load(); }, [due]); // eslint-disable-line react-hooks/exhaustive-deps
  const add = async () => {
    if (!form.email) return;
    await api.saveContact(form); setForm({}); load();
  };
  return (
    <Card elevation="sm">
      <SectionHead title="Network CRM" />
      <div className="flex items-center gap-2 text-xs">
        <button onClick={() => setDue(false)} className={`tag ${!due ? 'tag-solid' : 'tag-neutral'}`}>All</button>
        <button onClick={() => setDue(true)} className={`tag ${due ? 'tag-solid' : 'tag-neutral'}`}>Due follow-ups</button>
      </div>
      <div className="grid gap-2 sm:grid-cols-4">
        <Input placeholder="Name" value={form.name || ''} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Input placeholder="Email" value={form.email || ''} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <Input placeholder="Company" value={form.company || ''} onChange={(e) => setForm({ ...form, company: e.target.value })} />
        <Button variant="primary" onClick={add}>Add</Button>
      </div>
      <ul className="space-y-1.5">
        {list.map((c) => (
          <li key={c.id} className="flex items-center justify-between rounded-xl2 px-3 py-2 text-sm" style={{ background: 'var(--color-bg)' }}>
            <span>{c.name || c.email} <span className="text-xs text-muted">· {c.company} {c.title}</span></span>
            <span className="flex gap-2">
              <button onClick={() => api.nudgeContact(c.id)} className="text-xs" style={{ color: 'var(--color-accent)' }}>Nudge</button>
              <button onClick={() => api.deleteContact(c.id).then(load)} className="text-xs text-bad">✕</button>
            </span>
          </li>
        ))}
        {list.length === 0 && <li className="text-xs text-muted">No contacts{due ? ' due' : ''}.</li>}
      </ul>
    </Card>
  );
}
