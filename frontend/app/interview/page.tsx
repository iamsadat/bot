'use client';

import { useEffect, useState } from 'react';
import Nav from '@/components/Nav';
import Rail from '@/components/Rail';
import { Button, Card, PageHead, Select, Tag, Textarea } from '@/components/ui';
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
    <>
      <Rail />
      <div className="lg:pl-[220px]">
        <div className="lg:hidden"><Nav /></div>

        <main className="relative z-10 mx-auto min-h-screen max-w-4xl">
          <div className="space-y-4 px-6 pb-12 pt-6">
            <PageHead title="Interview prep" subtitle="Practice for a specific role and get rubric feedback on your answers." />

            <Card elevation="sm">
              <Select value={jobId} onChange={(e) => gen(e.target.value)}>
                <option value="">Select a job…</option>
                {jobs.map((j) => <option key={j.job_id} value={j.job_id}>{j.title} · {j.company}</option>)}
              </Select>
              {busy && <p className="text-xs text-muted">working…</p>}
            </Card>

            {!!questions.length && (
              <Card elevation="sm">
                <h2 className="text-sm font-semibold">Questions</h2>
                <ul className="space-y-2">
                  {questions.map((q, i) => (
                    <li key={i}>
                      <button
                        onClick={() => { setActive(q.question); setAnswer(''); setFb(null); }}
                        className="w-full rounded-xl2 p-2.5 text-left text-sm"
                        style={{
                          background: active === q.question ? 'var(--color-accent-100)' : 'var(--color-bg)',
                          border: `1px solid ${active === q.question ? 'var(--color-accent)' : 'var(--color-divider)'}`,
                        }}
                      >
                        <Tag tone="neutral" className="mr-2">{q.type}</Tag>
                        {q.question}
                      </button>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {active && (
              <Card elevation="sm">
                <h2 className="text-sm font-semibold">Your answer</h2>
                <p className="text-sm text-muted">{active}</p>
                <Textarea
                  value={answer} onChange={(e) => setAnswer(e.target.value)} rows={5}
                  placeholder="Answer out loud, then type the gist here for rubric feedback…"
                />
                <Button variant="primary" onClick={getFeedback}>Get feedback</Button>
                {fb && (
                  <div className="space-y-2">
                    <div className="flex flex-wrap gap-4 text-sm">
                      {Object.entries(fb.scores).map(([k, v]) => (
                        <span key={k} className="text-muted">
                          {k}: <span className="font-semibold" style={{ color: 'var(--color-accent)' }}>{Math.round((v as number) * 100)}%</span>
                        </span>
                      ))}
                      <span className="ml-auto text-muted">overall: <span className="font-semibold">{Math.round(fb.overall * 100)}%</span></span>
                    </div>
                    <ul className="list-disc pl-5 text-sm">
                      {fb.tips.map((t, i) => <li key={i}>{t}</li>)}
                    </ul>
                  </div>
                )}
              </Card>
            )}
          </div>
        </main>
      </div>
    </>
  );
}
