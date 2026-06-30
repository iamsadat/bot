'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api, recordPageview } from '@/lib/api';

export default function AtsScoreTool() {
  useEffect(() => { recordPageview('ats_tool'); }, []);

  const [resume, setResume] = useState('');
  const [jd, setJd] = useState('');
  const [res, setRes] = useState<{ score: number; matched: string[]; missing: string[]; suggestions: string[] } | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    if (!resume.trim() && !jd.trim()) return;
    setBusy(true);
    try { setRes(await api.atsScore(resume, jd)); } catch { setRes(null); } finally { setBusy(false); }
  };

  return (
    <main className="relative z-10 mx-auto min-h-screen max-w-4xl px-6 py-10">
      <Link href="/" className="font-extrabold tracking-tight">Job<span className="text-grad">Hunt</span></Link>
      <h1 className="mt-6 text-3xl font-extrabold">Free ATS match score</h1>
      <p className="mt-2 text-muted">Paste your résumé and a job description — see how well you match and what to add. No signup.</p>

      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <textarea value={resume} onChange={(e) => setResume(e.target.value)} rows={12}
          placeholder="Paste your résumé…" className="glass rounded-xl2 p-3 text-sm text-ink" />
        <textarea value={jd} onChange={(e) => setJd(e.target.value)} rows={12}
          placeholder="Paste the job description…" className="glass rounded-xl2 p-3 text-sm text-ink" />
      </div>
      <button onClick={run} disabled={busy}
        className="mt-3 rounded-full bg-grad px-6 py-2.5 font-semibold text-bg shadow-glow disabled:opacity-50">
        {busy ? 'Scoring…' : 'Score my match'}
      </button>

      {res && (
        <div className="mt-6 space-y-4">
          <div className="glass rounded-xl2 p-5 text-center shadow-card">
            <div className="text-5xl font-extrabold text-grad">{Math.round(res.score * 100)}%</div>
            <div className="mt-1 text-xs text-muted">keyword match</div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="glass rounded-xl2 p-4">
              <h3 className="mb-2 text-sm font-semibold text-good">Matched</h3>
              <div className="flex flex-wrap gap-1.5">
                {res.matched.map((k) => <span key={k} className="rounded bg-good/10 px-2 py-0.5 text-xs text-good">{k}</span>)}
              </div>
            </div>
            <div className="glass rounded-xl2 p-4">
              <h3 className="mb-2 text-sm font-semibold text-warn">Add these</h3>
              <div className="flex flex-wrap gap-1.5">
                {res.suggestions.map((k) => <span key={k} className="rounded bg-warn/10 px-2 py-0.5 text-xs text-warn">{k}</span>)}
              </div>
            </div>
          </div>
          <p className="text-center text-sm text-muted">
            Want JobHunt to <em>auto-tailor</em> your résumé to every job? <Link href="/onboarding" className="text-accent">Build your profile →</Link>
          </p>
        </div>
      )}
    </main>
  );
}
