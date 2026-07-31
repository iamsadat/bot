'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import Rail from '@/components/Rail';
import { Button, Card, Tag, Textarea } from '@/components/ui';
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
    <>
      <Rail />
      <div className="lg:pl-[220px]">
        <main className="relative z-10 mx-auto min-h-screen max-w-4xl px-6 py-10">
          <Link href="/" style={{ fontFamily: 'var(--font-heading)' }}>
            job<span style={{ color: 'var(--color-accent)' }}>hunt</span>
          </Link>
          <h1 className="mt-6">Free ATS match score</h1>
          <p className="mt-2 text-muted">Paste your résumé and a job description — see how well you match and what to add. No signup.</p>

          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            <Textarea value={resume} onChange={(e) => setResume(e.target.value)} rows={12} placeholder="Paste your résumé…" />
            <Textarea value={jd} onChange={(e) => setJd(e.target.value)} rows={12} placeholder="Paste the job description…" />
          </div>
          <Button variant="primary" onClick={run} disabled={busy} className="mt-3">
            {busy ? 'Scoring…' : 'Score my match'}
          </Button>

          {res && (
            <div className="mt-6 space-y-4">
              <Card elevation="sm" className="items-center text-center">
                <div style={{ fontFamily: 'var(--font-heading)', fontSize: 48, color: 'var(--color-accent)' }}>
                  {Math.round(res.score * 100)}%
                </div>
                <div className="text-xs text-muted">keyword match</div>
              </Card>
              <div className="grid gap-3 sm:grid-cols-2">
                <Card elevation="sm">
                  <h3 className="text-sm font-semibold text-good">Matched</h3>
                  <div className="flex flex-wrap gap-1.5">
                    {res.matched.map((k) => <Tag key={k} tone="accent-2">{k}</Tag>)}
                  </div>
                </Card>
                <Card elevation="sm">
                  <h3 className="text-sm font-semibold text-warn">Add these</h3>
                  <div className="flex flex-wrap gap-1.5">
                    {res.suggestions.map((k) => <Tag key={k} tone="outline">{k}</Tag>)}
                  </div>
                </Card>
              </div>
              <p className="text-center text-sm text-muted">
                Want JobHunt to <em>auto-tailor</em> your résumé to every job?{' '}
                <Link href="/onboarding" style={{ color: 'var(--color-accent)' }}>Build your profile →</Link>
              </p>
            </div>
          )}
        </main>
      </div>
    </>
  );
}
