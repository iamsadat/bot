'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { api, Doc, Job, ResumeDraft } from '@/lib/api';

function Rich({ s }: { s: string }) {
  // Render **bold** runs.
  const parts = s.split('**');
  return (
    <>
      {parts.map((p, i) => (i % 2 ? <strong key={i}>{p}</strong> : <span key={i}>{p}</span>))}
    </>
  );
}

function ResumeDoc({ d }: { d: ResumeDraft }) {
  const contact = [d.candidate_email, d.phone, d.location, ...Object.values(d.links || {})]
    .filter(Boolean)
    .join('  ·  ');
  return (
    <div className="rounded-xl bg-white p-8 text-[13px] leading-relaxed text-[#14161f] shadow-glow">
      <h1 className="text-center text-2xl font-bold">{d.candidate_name}</h1>
      <p className="mb-3 text-center text-xs text-gray-500">{contact}</p>
      {d.summary && <p className="mb-3 text-[12.5px]">{d.summary}</p>}
      {d.sections
        .filter((s) => s.kind !== 'summary')
        .map((s, i) => (
          <section key={i} className="mt-4">
            <h2 className="mb-1 border-b border-[#14161f] pb-1 text-[15px] font-bold">{s.title}</h2>
            {s.kind === 'skills' && s.body && <p className="text-[12.5px]">{s.body}</p>}
            {s.body && s.kind !== 'skills' && <p>{s.body}</p>}
            {(s.rows || []).map((r, j) => (
              <div key={j} className="mt-2">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-[13px]"><Rich s={r.left} /></span>
                  <span className="whitespace-nowrap text-[12px] text-gray-500">
                    {r.link ? <a href={r.link}>{r.right || r.link}</a> : r.right}
                  </span>
                </div>
                {!!r.bullets?.length && (
                  <ul className="ml-4 list-disc">
                    {r.bullets.map((b, k) => (
                      <li key={k} className="text-[12.5px]">{b.text}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </section>
        ))}
    </div>
  );
}

function money(n: number, ccy: string) {
  return `${ccy === 'USD' ? '$' : ccy === 'GBP' ? '£' : ccy + ' '}${Math.round(n / 1000)}k`;
}

const STATUSES = ['Saved', 'Applied', 'Assessment', 'Interview', 'Offer', 'Closed'];
const sel = 'rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-ink';

export default function ResumePreview({ job, onClose }: { job: Job | null; onClose: () => void }) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [salary, setSalary] = useState<any>(null);
  const [dlErr, setDlErr] = useState('');
  useEffect(() => {
    setDoc(null);
    setSalary(null);
    setDlErr('');
    if (job) {
      api.document(job.job_id).then((r) => setDoc(r.document)).catch(() => setDoc(null));
      // Salary intel is optional (needs Adzuna keys) — silently skip if off.
      api.salary(job.title, job.location || '')
        .then((s) => { if (s.sample > 0) setSalary(s); })
        .catch(() => {});
    }
  }, [job]);

  const download = async (jobId: string, fmt: string) => {
    setDlErr('');
    try {
      // credentials: the old <a href> sent the workspace cookie implicitly;
      // a cross-origin fetch (NEXT_PUBLIC_API_BASE in dev) would not.
      const res = await fetch(api.downloadUrl(jobId, fmt), { credentials: 'include' });
      if (!res.ok) throw new Error();
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `resume.${fmt}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setDlErr(`${fmt.toUpperCase()} isn't available right now — try again later.`);
    }
  };

  return (
    <AnimatePresence>
      {job && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            className="fixed right-0 top-0 z-50 flex h-full w-full max-w-2xl flex-col gap-4 overflow-y-auto bg-bg/95 p-6 shadow-glow"
            initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
            transition={{ type: 'spring', damping: 28, stiffness: 240 }}
          >
            <div className="flex items-start justify-between">
              <div>
                <h2 className="text-lg font-semibold">{job.title}</h2>
                <p className="text-sm text-muted">{job.company} · {job.location}</p>
              </div>
              <button onClick={onClose} className="glass rounded-full px-3 py-1 text-sm">✕</button>
            </div>

            {salary && (
              <div className="glass rounded-xl2 p-3 text-sm">
                <span className="text-muted">Market pay </span>
                <span className="font-semibold text-ink">
                  {money(salary.p10, salary.currency)}–{money(salary.p90, salary.currency)}
                </span>
                <span className="text-muted"> · median </span>
                <span className="font-semibold text-grad">{money(salary.median, salary.currency)}</span>
                <span className="text-muted"> ({salary.sample} postings)</span>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              {['pdf', 'docx', 'html', 'txt'].map((f) => (
                <button
                  key={f}
                  onClick={() => download(job.job_id, f)}
                  className="glass rounded-lg px-3 py-1.5 text-xs font-medium transition hover:border-white/20"
                >
                  ↓ {f.toUpperCase()}
                </button>
              ))}
              {dlErr && <span className="text-xs text-warn">{dlErr}</span>}
              <select
                key={job.job_id}
                defaultValue={job.status}
                onChange={(e) => api.setJobStatus(job.job_id, e.target.value)}
                className={sel}
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
              <button
                onClick={async () => {
                  try {
                    const r = await api.publish(job.job_id);
                    window.open(r.url, '_blank');
                  } catch {/* needs a tailored draft */}
                }}
                className="ml-auto rounded-lg px-3 py-1.5 text-xs font-medium glass transition hover:border-white/20"
              >
                ↗ Publish
              </button>
              <button
                onClick={() => api.approve(job.job_id).then(onClose)}
                className="rounded-lg bg-grad px-4 py-1.5 text-xs font-semibold text-bg"
              >
                Approve & apply
              </button>
            </div>

            {doc?.draft ? (
              <ResumeDoc d={doc.draft} />
            ) : (
              <div className="glass rounded-xl2 p-8 text-center text-sm text-muted">
                {doc ? 'No tailored résumé yet — run a hunt for this role.' : 'Loading…'}
              </div>
            )}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
