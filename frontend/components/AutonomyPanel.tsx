'use client';

import { useEffect, useState } from 'react';
import { api, Autonomy } from '@/lib/api';
import { Card, CardKicker, Field, Input } from './ui';

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

  const label = a.auto_apply ? 'Auto-apply' : 'Co-pilot';
  const note = !a.ats_connected
    ? 'Connect an ATS to enable auto-apply.'
    : a.auto_apply
      ? 'Submits matches automatically.'
      : 'Fills the form, leaves the final submit to you.';

  return (
    <Card elevation="sm">
      <div className="flex items-center justify-between">
        <CardKicker>Autonomy</CardKicker>
        {saving && <span className="text-[10px] text-muted">saving…</span>}
      </div>
      <div className="text-[17px] leading-[1.15]" style={{ fontFamily: 'var(--font-heading)' }}>
        {label}
      </div>
      <div className="text-[11.5px] leading-[1.4] text-muted">{note}</div>

      <button
        type="button"
        onClick={() => update({ auto_apply: !a.auto_apply })}
        disabled={!a.ats_connected}
        aria-label="Toggle auto-apply"
        aria-pressed={a.auto_apply}
        className="relative h-[25px] w-[46px] rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        style={{ background: a.auto_apply ? 'var(--color-accent-2)' : 'var(--color-neutral-400)' }}
      >
        <span
          className="absolute top-[3px] h-[19px] w-[19px] rounded-full transition-all"
          style={{ left: a.auto_apply ? 24 : 3, background: 'var(--color-bg)' }}
        />
      </button>

      <div className="mt-1 grid grid-cols-2 gap-3">
        <Field label="Daily cap">
          <Input
            type="number"
            min={0}
            defaultValue={a.daily_apply_cap}
            onBlur={(e) => update({ daily_apply_cap: parseInt(e.target.value || '0', 10) })}
          />
        </Field>
        <Field label={`Min match ${Math.round(a.relevance_floor * 100)}%`}>
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
        </Field>
      </div>

      <div
        className="flex items-center justify-between rounded-[16px] px-3 py-2 text-[11px]"
        style={{ background: 'var(--color-bg)' }}
      >
        <span className="text-muted">Applied today</span>
        <span className="font-semibold">
          {a.applied_today}{a.effective_cap ? ` / ${a.effective_cap}` : ''}
        </span>
      </div>
      {a.continuous && (
        <p className="text-center text-[10px]" style={{ color: 'var(--color-accent-2-700)' }}>
          ● continuous discovery on
        </p>
      )}
    </Card>
  );
}
