'use client';

import { useState } from 'react';
import { api, Doc, ResumeDraft } from '@/lib/api';
import { Button, Input, Textarea } from './ui';

// Hand edits to one job's tailored résumé. Works on a copy; Save stores it
// (downloads follow, and the background AI rewrite leaves it alone), Cancel
// drops it.
export default function ResumeEditor({ jobId, draft, onSaved, onCancel }: {
  jobId: string; draft: ResumeDraft; onSaved: (doc: Doc) => void; onCancel: () => void;
}) {
  const [d, setD] = useState<ResumeDraft>(() => structuredClone(draft));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  // Apply a change to a fresh copy so React sees a new object.
  const edit = (fn: (c: ResumeDraft) => void) => setD((prev) => { const c = structuredClone(prev); fn(c); return c; });

  const save = async () => {
    setSaving(true);
    setErr('');
    try {
      onSaved((await api.saveDocument(jobId, d)).document);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 rounded-[18px] p-4" style={{ background: 'var(--color-surface)' }}>
      <div>
        <p className="mb-1 text-xs text-muted">Summary</p>
        <Textarea rows={4} value={d.summary} onChange={(ev) => { const v = ev.target.value; edit((c) => { c.summary = v; }); }} />
      </div>
      {d.sections.map((s, i) => s.kind !== 'summary' && (
        <div key={i}>
          <p className="mb-1 text-xs uppercase tracking-[0.12em]" style={{ color: 'var(--color-accent)' }}>{s.title}</p>
          {(s.body || s.kind === 'skills') && (
            <Textarea
              rows={s.kind === 'skills' ? 4 : 2} value={s.body || ''}
              placeholder={s.kind === 'skills' ? 'Category: a, b — one per line' : ''}
              onChange={(ev) => { const v = ev.target.value; edit((c) => { c.sections[i].body = v; }); }}
            />
          )}
          <div className="space-y-2">
            {(s.rows || []).map((r, j) => (
              <div key={j} className="rounded-xl2 p-3" style={{ background: 'var(--color-bg)' }}>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Input placeholder="Heading (**bold** allowed)" value={r.left} onChange={(ev) => { const v = ev.target.value; edit((c) => { c.sections[i].rows![j].left = v; }); }} />
                  <Input placeholder="Dates / place" value={r.right || ''} onChange={(ev) => { const v = ev.target.value; edit((c) => { c.sections[i].rows![j].right = v; }); }} />
                </div>
                {(r.bullets || []).map((b, k) => (
                  <div key={k} className="mt-2 flex items-start gap-2">
                    <Textarea
                      rows={2} className="flex-1" value={b.text}
                      onChange={(ev) => { const v = ev.target.value; edit((c) => { c.sections[i].rows![j].bullets![k].text = v; }); }}
                    />
                    <button onClick={() => edit((c) => { c.sections[i].rows![j].bullets!.splice(k, 1); })} className="text-[11px] text-bad">Remove</button>
                  </div>
                ))}
                <button
                  onClick={() => edit((c) => { const row = c.sections[i].rows![j]; row.bullets = [...(row.bullets || []), { text: '' }]; })}
                  className="mt-2 text-[11px] text-muted"
                >
                  + Add bullet
                </button>
              </div>
            ))}
          </div>
        </div>
      ))}
      {err && <p className="m-0 text-xs text-warn">{err}</p>}
      <div className="flex gap-2">
        <Button variant="primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
        <Button variant="secondary" onClick={onCancel} disabled={saving}>Cancel</Button>
      </div>
    </div>
  );
}
