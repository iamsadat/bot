'use client';

import { useEffect, useState } from 'react';
import Nav from '@/components/Nav';
import { api, InterviewFeedback, Job } from '@/lib/api';

export default function InterviewPrep() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobId, setJobId] = useState('');
  const [questions, setQuestions] = useState<{ type: string; question: string }[]>([]);
  const [answer, setAnswer] = useState('');
  const [active, setActive] = useState('');
  const [fb, setFb] = useState<InterviewFeedback | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.jobs().then((r) => setJobs(r.jobs || [])).catch(() => {}); }, []);

  const gen = async (id: string) => {
    setJobId(id); setQuestions([]); setFb(null);
    if (!id) return;
    setBusy(true);
    try { setQuestions((await api.interviewQuestions(id)).questions || []); } finally { setBusy(false); }
  };
  const getFeedback = async () => {
    if (!active || !answer.trim()) return;
    setBusy(true);
    try { setFb(await api.interviewFeedback(active, answer)); } finally { setBusy(false); }
  };

  return (
    <main className="relative z-10 mx-auto min-h-screen max-w-4xl">
      <Nav />
      <div className="space-y-4 px-6 pb-12">
        <section className="glass rounded-xl2 p-4 shadow-card">
          <h2 className="mb-3 text-sm font-semibold">Practice interview for a role</h2>
          <select value={jobId} onChange={(e) => gen(e.target.value)}
            className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-ink">
            <option value="">Select a job…</option>
            {jobs.map((j) => <option key={j.job_id} value={j.job_id}>{j.title} · {j.company}</option>)}
          </select>
          {busy && <p className="mt-2 text-xs text-muted">working…</p>}
        </section>

        {!!questions.length && (
          <section className="glass rounded-xl2 p-4 shadow-card">
            <h2 className="mb-3 text-sm font-semibold">Questions</h2>
            <ul className="space-y-2">
              {questions.map((q, i) => (
                <li key={i}>
                  <button onClick={() => { setActive(q.question); setAnswer(''); setFb(null); }}
                    className={`w-full rounded-lg border p-2.5 text-left text-sm transition ${active === q.question ? 'border-accent/50 bg-accent/5' : 'border-white/8 hover:border-white/20'}`}>
                    <span className="mr-2 rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-muted">{q.type}</span>
                    {q.question}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        {active && (
          <section className="glass rounded-xl2 p-4 shadow-card">
            <h2 className="mb-2 text-sm font-semibold">Your answer</h2>
            <p className="mb-2 text-sm text-muted">{active}</p>
            <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={5}
              placeholder="Answer out loud, then type the gist here for rubric feedback…"
              className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-ink" />
            <button onClick={getFeedback} className="mt-2 rounded-lg bg-grad px-4 py-2 text-sm font-semibold text-bg">Get feedback</button>
            {fb && (
              <div className="mt-3 space-y-2">
                <div className="flex gap-4 text-sm">
                  {Object.entries(fb.scores).map(([k, v]) => (
                    <span key={k} className="text-muted">{k}: <span className="font-semibold text-grad">{Math.round((v as number) * 100)}%</span></span>
                  ))}
                  <span className="ml-auto text-muted">overall: <span className="font-semibold text-ink">{Math.round(fb.overall * 100)}%</span></span>
                </div>
                <ul className="list-disc pl-5 text-sm text-ink/90">
                  {fb.tips.map((t, i) => <li key={i}>{t}</li>)}
                </ul>
              </div>
            )}
          </section>
        )}
      </div>
    </main>
  );
}
