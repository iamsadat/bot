'use client';

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { api, Autonomy } from '@/lib/api';

export default function AutonomyPanel() {
  const [a, setA] = useState<Autonomy | null>(null);
  const [saving, setSaving] = useState(false);

  const load = () => api.autonomy().then(setA).catch(() => {});
  useEffect(() => { load(); }, []);

  const update = async (patch: Partial<Autonomy>) => {
    if (!a) return;
    setSaving(true);
    setA({ ...a, ...patch });
    try {
      await api.setAutonomy(patch);
      await load();
    } finally {
      setSaving(false);
    }
  };

  if (!a) return null;

  return (
    <div className="glass rounded-xl2 p-4 shadow-card">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold">Autonomy</h3>
        {saving && <span className="text-[10px] text-muted">saving…</span>}
      </div>

      <button
        onClick={() => update({ auto_apply: !a.auto_apply })}
        disabled={!a.ats_connected}
        className={`flex w-full items-center justify-between rounded-lg border p-3 transition ${
          a.auto_apply
            ? 'border-good/40 bg-good/10'
            : 'border-white/10 bg-white/[0.02] hover:border-white/20'
        } ${!a.ats_connected ? 'cursor-not-allowed opacity-50' : ''}`}
      >
        <span className="text-left">
          <span className="block text-sm font-medium">Auto-apply</span>
          <span className="block text-[11px] text-muted">
            {a.ats_connected ? 'Submits matches automatically' : 'Connect an ATS to enable'}
          </span>
        </span>
        <span className={`relative h-6 w-11 rounded-full transition ${a.auto_apply ? 'bg-grad' : 'bg-white/15'}`}>
          <motion.span
            layout
            className="absolute top-0.5 h-5 w-5 rounded-full bg-white"
            style={{ left: a.auto_apply ? 22 : 2 }}
          />
        </span>
      </button>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <label className="text-[11px] text-muted">
          Daily cap
          <input
            type="number"
            min={0}
            defaultValue={a.daily_apply_cap}
            onBlur={(e) => update({ daily_apply_cap: parseInt(e.target.value || '0', 10) })}
            className="mt-1 w-full rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-sm text-ink"
          />
        </label>
        <label className="text-[11px] text-muted">
          Min match {Math.round(a.relevance_floor * 100)}%
          <input
            type="range"
            min={0}
            max={100}
            defaultValue={Math.round(a.relevance_floor * 100)}
            onMouseUp={(e) =>
              update({ relevance_floor: parseInt((e.target as HTMLInputElement).value, 10) / 100 })
            }
            className="mt-2 w-full accent-accent"
          />
        </label>
      </div>

      <div className="mt-3 flex items-center justify-between rounded-lg bg-white/[0.02] px-3 py-2 text-[11px]">
        <span className="text-muted">Applied today</span>
        <span className="font-semibold text-ink">
          {a.applied_today}{a.effective_cap ? ` / ${a.effective_cap}` : ''}
        </span>
      </div>
      {a.continuous && (
        <p className="mt-2 text-center text-[10px] text-good">● continuous discovery on</p>
      )}
    </div>
  );
}
