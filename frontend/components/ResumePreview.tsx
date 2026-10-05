'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { api, apiFetch, describeSubmission, Doc, Job, ResumeDraft } from '@/lib/api';
import { Button, Card, CardTitle, Meter, Select, Tag } from './ui';

function Rich({ s }: { s: string }) {
  // Render **bold** runs.
  const parts = s.split('**');
  return (
    <>
      {parts.map((p, i) => (i % 2 ? <strong key={i}>{p}</strong> : <span key={i}>{p}</span>))}
    </>
  );
}

// Bullets backed by an evidence id get the "freshly tailored" sweep; plain
// bullets (e.g. older documents) render as normal text in the same box.
function SweepBullet({ text, delay, sweep }: { text: string; delay: number; sweep: boolean }) {
  return (
    <div
      className="rounded-[7px] px-[7px] py-[4px] text-[9.5px] leading-[1.5]"
      style={sweep ? {
        backgroundImage:
          'linear-gradient(100deg, transparent 20%, color-mix(in srgb, var(--color-accent-2) 34%, transparent) 42%, transparent 64%)',
        backgroundSize: '220% 100%',
        animationName: 'jh-sweep',
        animationDuration: '5.4s',
        animationTimingFunction: 'ease-in-out',
        animationIterationCount: 'infinite',
        animationDelay: `${delay}s`,
      } : undefined}
    >
      {text}
    </div>
  );
}

function ResumeDoc({ d }: { d: ResumeDraft }) {
  const contact = [d.candidate_email, d.phone, d.location, ...Object.values(d.links || {})]
    .filter(Boolean)
    .join('  ·  ');
  return (
    <div
      className="rounded-[14px] p-7 text-[#201e1d]"
      style={{ background: 'var(--color-neutral-100)', boxShadow: 'var(--shadow-lg)' }}
    >
      <h1 className="text-center text-[22px]">{d.candidate_name}</h1>
      <p className="mb-3 text-center text-[9.5px] text-muted">{contact}</p>
      {d.summary && <p className="mb-3 text-[9.5px] leading-[1.5]">{d.summary}</p>}
      {d.sections
        .filter((s) => s.kind !== 'summary')
        .map((s, i) => (
          <section key={i} className="mt-4">
            <h2
              className="mb-2 text-[10px] uppercase tracking-[0.12em]"
              style={{ fontFamily: 'var(--font-body)', fontWeight: 400, color: 'var(--color-accent)', margin: 0 }}
            >
              {s.title}
            </h2>
            {s.kind === 'skills' && s.body && <p className="text-[9.5px] text-muted">{s.body}</p>}
            {s.body && s.kind !== 'skills' && <p className="text-[9.5px] leading-[1.5]">{s.body}</p>}
            {(s.rows || []).map((r, j) => (
              <div key={j} className="mt-2.5 first:mt-0">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-[10.5px] font-bold"><Rich s={r.left} /></span>
                  <span className="whitespace-nowrap text-[9.5px] text-muted">
                    {r.link ? <a href={r.link}>{r.right || r.link}</a> : r.right}
                  </span>
                </div>
                {!!r.bullets?.length && (
                  <div className="mt-1 flex flex-col gap-1">
                    {r.bullets.map((b, k) => (
                      <SweepBullet key={k} text={b.text} delay={k * 0.5} sweep={!!b.evidence_id} />
                    ))}
                  </div>
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

// Why the match percentage is what it is. A bare number invites the reader to
// distrust it — especially a low one — so each component is shown with the
// skills the job asked for and whether the candidate has them.
function MatchBreakdown({ job }: { job: Job }) {
  const b = job.score_breakdown;
  if (!b || typeof b.total !== 'number') return null;
  const pct = (n: number) => `${Math.round(n * 100)}%`;
  const rows: { label: string; value: number; note?: string; uncounted?: boolean }[] = [
    { label: 'Role match', value: b.title },
    {
      label: 'Skills they asked for',
      value: b.skills,
      note: b.skills_scored === false ? 'short description — not counted' : undefined,
      // An uncounted component must not read as a 0% the candidate scored.
      uncounted: b.skills_scored === false,
    },
    {
      label: 'Level',
      value: b.seniority,
      note: b.posting_level_name
        ? `${b.posting_level_name} role${b.candidate_level_name ? ` · you: ${b.candidate_level_name}` : ''}`
        : 'level not stated',
    },
    { label: 'Location', value: b.location },
  ];
  return (
    <Card elevation="sm">
      <div className="flex items-baseline justify-between">
        <CardTitle>Why {pct(b.total)} match</CardTitle>
      </div>
      <div className="flex flex-col gap-2">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center gap-2 text-xs">
            <span className="w-40 shrink-0 text-muted">{r.label}</span>
            <Meter value={r.uncounted ? 0 : r.value} className={`flex-1 ${r.uncounted ? 'opacity-40' : ''}`} />
            <span className="w-9 shrink-0 text-right tabular-nums">{r.uncounted ? 'n/a' : pct(r.value)}</span>
          </div>
        ))}
      </div>
      {(b.matched_keywords?.length || b.missing_keywords?.length) ? (
        <div className="mt-1 flex flex-wrap gap-1.5">
          {b.matched_keywords?.map((k) => (
            <Tag key={`m${k}`} tone="accent-2">{k}</Tag>
          ))}
          {b.missing_keywords?.map((k) => (
            <Tag key={`x${k}`} tone="neutral">{k}</Tag>
          ))}
        </div>
      ) : null}
      {rows.map((r) => r.note && (
        <p key={`n${r.label}`} className="text-[11px] text-muted">{r.label}: {r.note}</p>
      ))}
    </Card>
  );
}

export default function ResumePreview({ job, onClose }: { job: Job | null; onClose: () => void }) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [salary, setSalary] = useState<any>(null);
  const [dlErr, setDlErr] = useState('');
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState(false);
  const [approveMsg, setApproveMsg] = useState<{ text: string; warn?: boolean } | null>(null);
  useEffect(() => {
    setDoc(null);
    setSalary(null);
    setDlErr('');
    setApproved(false);
    setApproveMsg(null);
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
      // apiFetch sends the workspace cookie (an <a href> did so implicitly; a
      // cross-origin fetch in dev would not) and the access code, if any.
      const res = await apiFetch(api.downloadUrl(jobId, fmt));
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

  // The drawer stays open after approving so the user can read what actually
  // happened — submitted, left for them to finish, or waiting in a co-pilot
  // browser window for their Submit.
  const approve = async (jobId: string) => {
    setApproving(true);
    setApproveMsg(null);
    try {
      const r = await api.approve(jobId);
      setApproved(true);
      setApproveMsg(describeSubmission(r.submission) ?? { text: 'Approved.' });
    } catch (e) {
      setApproveMsg({ text: e instanceof Error ? e.message : String(e), warn: true });
    } finally {
      setApproving(false);
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
            className="fixed right-0 top-0 z-50 flex h-full w-full max-w-2xl flex-col gap-4 overflow-y-auto p-6"
            style={{ background: 'var(--color-bg)', boxShadow: 'var(--shadow-lg)' }}
            initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
            transition={{ type: 'spring', damping: 28, stiffness: 240 }}
          >
            <div className="flex items-start justify-between">
              <div>
                <h2 className="m-0 text-[22px]">{job.title}</h2>
                <p className="mt-1 text-sm text-muted">{job.company} · {job.location}</p>
              </div>
              <Button variant="secondary" icon onClick={onClose} aria-label="Close">✕</Button>
            </div>

            <MatchBreakdown job={job} />

            {salary && (
              <Card elevation="sm" className="text-sm">
                <span className="text-muted">Market pay </span>
                <span className="font-semibold">
                  {money(salary.p10, salary.currency)}–{money(salary.p90, salary.currency)}
                </span>
                <span className="text-muted"> · median </span>
                <span className="font-semibold" style={{ color: 'var(--color-accent)' }}>
                  {money(salary.median, salary.currency)}
                </span>
                <span className="text-muted"> ({salary.sample} postings)</span>
              </Card>
            )}

            <div className="flex flex-wrap items-center gap-2">
              {['pdf', 'docx', 'html', 'txt'].map((f) => (
                <Button key={f} variant="secondary" onClick={() => download(job.job_id, f)}>
                  ↓ {f.toUpperCase()}
                </Button>
              ))}
              {dlErr && <span className="text-xs" style={{ color: 'var(--color-accent-500)' }}>{dlErr}</span>}
              <Select
                key={job.job_id}
                defaultValue={job.status}
                onChange={(e) => api.setJobStatus(job.job_id, e.target.value)}
                style={{ width: 'auto' }}
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </Select>
              <Button
                variant="secondary"
                className="ml-auto"
                onClick={async () => {
                  try {
                    const r = await api.publish(job.job_id);
                    window.open(r.url, '_blank');
                  } catch {/* needs a tailored draft */}
                }}
              >
                ↗ Publish
              </Button>
              <Button
                variant="primary"
                onClick={() => approve(job.job_id)}
                disabled={approving || approved}
              >
                {approved ? 'Approved' : approving ? 'Approving…' : 'Approve & apply'}
              </Button>
            </div>
            {approveMsg && (
              <p className={`m-0 text-xs ${approveMsg.warn ? 'text-warn' : 'text-muted'}`}>{approveMsg.text}</p>
            )}

            {doc?.draft ? (
              <ResumeDoc d={doc.draft} />
            ) : (
              <div
                className="rounded-[26px] p-8 text-center text-sm text-muted"
                style={{ background: 'var(--color-surface)' }}
              >
                {doc ? 'No tailored résumé yet — run a hunt for this role.' : 'Loading…'}
              </div>
            )}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
