'use client';

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import Nav from '@/components/Nav';
import AnimatedNumber from '@/components/AnimatedNumber';
import { api, Contact, RadarSettings } from '@/lib/api';
import { usePoll } from '@/lib/useLive';

function Stat({ label, value, accent, pct }: { label: string; value: number; accent?: boolean; pct?: boolean }) {
  return (
    <div className="glass rounded-xl2 p-4 shadow-card">
      <div className={`text-3xl font-extrabold tabular-nums ${accent ? 'text-grad' : 'text-ink'}`}>
        {pct ? `${Math.round(value * 100)}%` : <AnimatedNumber value={value} />}
      </div>
      <div className="mt-1 text-xs text-muted">{label}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="glass rounded-xl2 p-4 shadow-card">
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export default function Insights() {
  const m = usePoll(() => api.metrics(), 4000);
  const a = usePoll(() => api.analytics(), 6000);
  const radar = usePoll(() => api.radar(), 8000);
  const skills = usePoll(() => api.skillGaps(), 10000);

  return (
    <main className="relative z-10 mx-auto min-h-screen max-w-6xl">
      <Nav />
      <div className="space-y-4 px-6 pb-12">
        <motion.div
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          className="grid grid-cols-2 gap-3 sm:grid-cols-5"
        >
          <Stat label="Discovered" value={m?.discovered ?? 0} accent />
          <Stat label="Tailored" value={m?.tailored ?? 0} />
          <Stat label="Applied" value={m?.applied ?? 0} />
          <Stat label="Interviews" value={m?.interview ?? 0} />
          <Stat label="Offers" value={m?.offer ?? 0} accent />
        </motion.div>

        <div className="grid gap-3 sm:grid-cols-4">
          <Stat label="Callback rate" value={m?.callback_rate ?? 0} pct accent />
          <Stat label="Evidence coverage" value={m?.evidence_coverage ?? 0} pct />
          <Stat label="Day streak" value={m?.streak ?? 0} />
          <Stat label="Weekly goal" value={m?.weekly_progress ?? 0} pct />
        </div>

        {/* A/B résumé strategy */}
        {!!a?.variants?.length && (
          <Section title="Résumé strategy A/B (which converts to interviews)">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr><th className="py-1">Variant</th><th>Sent</th><th>Interviews</th><th>Rate</th></tr>
              </thead>
              <tbody>
                {a.variants.map((v) => (
                  <tr key={v.name} className="border-t border-white/5">
                    <td className="py-1.5">
                      {v.name}{a.winner === v.name && <span className="ml-2 rounded bg-good/15 px-1.5 py-0.5 text-[10px] text-good">winner</span>}
                    </td>
                    <td>{v.impressions}</td><td>{v.successes}</td>
                    <td className="text-grad font-semibold">{Math.round(v.success_rate * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        )}

        <CareerRadar radar={radar} />

        {/* Skills to grow */}
        {!!skills?.gaps?.length && (
          <Section title="Skills to grow (most-missed across your matches)">
            <ul className="space-y-2">
              {skills.gaps.slice(0, 8).map((g) => (
                <li key={g.skill} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="rounded bg-accent/10 px-2 py-0.5 text-accent">{g.skill}</span>
                  <span className="text-xs text-muted">missed in {g.count} role(s)</span>
                  {g.resources?.slice(0, 2).map((r) => (
                    <a key={r.url} href={r.url} target="_blank" rel="noreferrer"
                       className="text-xs text-accent2 underline">{r.title}</a>
                  ))}
                </li>
              ))}
            </ul>
          </Section>
        )}

        <Contacts />
      </div>
    </main>
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
    <Section title="Career Radar — passive, always-on">
      {s && (
        <div className="grid gap-3 sm:grid-cols-2">
          <button
            onClick={() => save({ radar_enabled: !s.radar_enabled })}
            className={`flex items-center justify-between rounded-lg border p-3 ${s.radar_enabled ? 'border-good/40 bg-good/10' : 'border-white/10'}`}
          >
            <span className="text-sm font-medium">Radar {s.radar_enabled ? 'on' : 'off'}</span>
            <span className="text-[11px] text-muted">{saving ? 'saving…' : 'pings only for roles that beat your current comp/title'}</span>
          </button>
          <label className="text-[11px] text-muted">Current salary
            <input type="number" defaultValue={s.current_salary ?? undefined}
              onBlur={(e) => save({ current_salary: parseInt(e.target.value || '0', 10) || null })}
              className="mt-1 w-full rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-sm text-ink" />
          </label>
          <label className="text-[11px] text-muted">Current title
            <input defaultValue={s.current_title}
              onBlur={(e) => save({ current_title: e.target.value })}
              className="mt-1 w-full rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-sm text-ink" />
          </label>
          <label className="text-[11px] text-muted">Watchlist keywords (comma-sep)
            <input defaultValue={(s.radar_keywords || []).join(', ')}
              onBlur={(e) => save({ radar_keywords: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })}
              className="mt-1 w-full rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-sm text-ink" />
          </label>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-4 text-sm">
        {mv && <span className="text-muted">Market value (median): <span className="font-semibold text-grad">{Math.round(mv.median / 1000)}k {mv.currency}</span></span>}
        {!!radar?.hits?.length && <span className="text-muted">{radar.hits.length} radar hit(s) waiting</span>}
      </div>
    </Section>
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
    <Section title="Network CRM">
      <div className="mb-3 flex items-center gap-2 text-xs">
        <button onClick={() => setDue(false)} className={`rounded-full px-3 py-1 ${!due ? 'bg-grad text-bg' : 'glass'}`}>All</button>
        <button onClick={() => setDue(true)} className={`rounded-full px-3 py-1 ${due ? 'bg-grad text-bg' : 'glass'}`}>Due follow-ups</button>
      </div>
      <div className="mb-3 grid gap-2 sm:grid-cols-4">
        <input placeholder="Name" value={form.name || ''} onChange={(e) => setForm({ ...form, name: e.target.value })} className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-sm" />
        <input placeholder="Email" value={form.email || ''} onChange={(e) => setForm({ ...form, email: e.target.value })} className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-sm" />
        <input placeholder="Company" value={form.company || ''} onChange={(e) => setForm({ ...form, company: e.target.value })} className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-sm" />
        <button onClick={add} className="rounded-lg bg-grad px-3 py-1.5 text-sm font-semibold text-bg">Add</button>
      </div>
      <ul className="space-y-1.5">
        {list.map((c) => (
          <li key={c.id} className="flex items-center justify-between rounded-lg bg-white/[0.02] px-3 py-2 text-sm">
            <span>{c.name || c.email} <span className="text-xs text-muted">· {c.company} {c.title}</span></span>
            <span className="flex gap-2">
              <button onClick={() => api.nudgeContact(c.id)} className="text-xs text-accent">Nudge</button>
              <button onClick={() => api.deleteContact(c.id).then(load)} className="text-xs text-bad">✕</button>
            </span>
          </li>
        ))}
        {list.length === 0 && <li className="text-xs text-muted">No contacts{due ? ' due' : ''}.</li>}
      </ul>
    </Section>
  );
}
