'use client';

import { useState } from 'react';
import { motion } from 'framer-motion';
import Nav from '@/components/Nav';
import SaveProgressBanner from '@/components/SaveProgressBanner';
import AnimatedNumber from '@/components/AnimatedNumber';
import Kanban from '@/components/Kanban';
import ReasoningFeed from '@/components/ReasoningFeed';
import AutonomyPanel from '@/components/AutonomyPanel';
import ResumePreview from '@/components/ResumePreview';
import { api, Approval, Job } from '@/lib/api';
import { usePoll } from '@/lib/useLive';

function ago(epochSeconds: number): string {
  const diffMs = Date.now() - epochSeconds * 1000;
  const s = Math.max(0, Math.round(diffMs / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return `${h}h ago`;
}

function SourcesPanel() {
  const sourcesData = usePoll(() => api.sources(), 5000);
  const sources = sourcesData?.sources || [];

  return (
    <section className="glass mt-4 rounded-xl2 p-4 shadow-card">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Sources</h2>
        {sourcesData && <span className="text-xs text-muted">page {sourcesData.page}</span>}
      </div>
      {sources.length === 0 ? (
        <p className="text-xs text-muted">No sweep yet — hit Run hunt or Fetch more.</p>
      ) : (
        <div className="space-y-2">
          {sources.map((s) => (
            <div
              key={s.name}
              className="flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.02] p-2.5"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className={`text-lg leading-none ${s.status === 'ok' ? 'text-good' : 'text-warn'}`}>•</span>
                <span className="truncate text-sm font-medium">{s.name}</span>
              </div>
              <div className="flex shrink-0 items-center gap-3 text-xs text-muted">
                <span>{s.jobs} jobs</span>
                <span>checked {ago(s.checked_at)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Stat({
  label, value, accent, onClick,
}: { label: string; value: number; accent?: boolean; onClick?: () => void }) {
  return (
    <div
      onClick={onClick}
      className={`glass rounded-xl2 p-4 shadow-card ${onClick ? 'cursor-pointer transition hover:border-white/20' : ''}`}
    >
      <div className={`text-3xl font-extrabold tabular-nums ${accent ? 'text-grad' : 'text-ink'}`}>
        <AnimatedNumber value={value} />
      </div>
      <div className="mt-1 text-xs text-muted">{label}</div>
    </div>
  );
}

function ApprovalsPanel() {
  const approvalsData = usePoll(() => api.approvals(), 2500);
  const [busyId, setBusyId] = useState<string | null>(null);
  const approvals = approvalsData?.approvals || [];

  const decide = async (a: Approval, decision: 'approve' | 'reject') => {
    setBusyId(a.request_id);
    try {
      await api.approve(a.request_id, decision);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <motion.section
      initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
      className="glass mt-3 rounded-xl2 p-4 shadow-card"
    >
      <h2 className="mb-3 text-sm font-semibold">Pending approval</h2>
      {approvals.length === 0 ? (
        <p className="text-xs text-muted">Nothing waiting on you.</p>
      ) : (
        <div className="space-y-2">
          {approvals.map((a) => (
            <div
              key={a.request_id}
              className="flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.02] p-2.5"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{a.title}</div>
                <div className="truncate text-xs text-muted">{a.company}</div>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  onClick={() => decide(a, 'approve')}
                  disabled={busyId === a.request_id}
                  className="rounded-full bg-grad px-3 py-1.5 text-xs font-semibold text-bg disabled:opacity-40"
                >
                  Approve
                </button>
                <button
                  onClick={() => decide(a, 'reject')}
                  disabled={busyId === a.request_id}
                  className="glass rounded-full px-3 py-1.5 text-xs font-medium text-warn disabled:opacity-40"
                >
                  Reject
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </motion.section>
  );
}

export default function Dashboard() {
  const status = usePoll(() => api.status(), 2500);
  const jobsData = usePoll(() => api.jobs(), 2500);
  const [selected, setSelected] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [showApprovals, setShowApprovals] = useState(false);
  const [fetchMsg, setFetchMsg] = useState<{ text: string; warn?: boolean } | null>(null);
  const jobs = jobsData?.jobs || [];

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  };

  const fetchMore = async () => {
    setFetchMsg(null);
    setBusy(true);
    try {
      const r = await api.discover();
      if (r.added > 0) {
        setFetchMsg({ text: `Fetched ${r.seen} postings · ${r.added} new · ${r.duplicates} already seen` });
      } else if (r.seen > 0) {
        let text = `Checked ${r.seen} postings — nothing new.`;
        const sources = await api.sources();
        if (sources.ats_connected) {
          text += ' Connected job boards return their whole board at once, so new roles only appear when a company posts one.';
        }
        setFetchMsg({ text });
      } else {
        setFetchMsg({ text: 'No postings came back. Check the source panel below.' });
      }
    } catch (e) {
      setFetchMsg({ text: e instanceof Error ? e.message : String(e), warn: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="relative z-10 mx-auto min-h-screen max-w-7xl">
      <Nav
        right={
          <>
            <button
              onClick={fetchMore}
              disabled={busy || !status?.has_profile}
              className="glass rounded-full px-4 py-2 text-sm font-medium transition hover:border-white/20 disabled:opacity-40"
            >
              Fetch more
            </button>
            <button
              onClick={() => run(api.startHunt)}
              disabled={busy || !status?.has_profile}
              className="rounded-full bg-grad px-4 py-2 text-sm font-semibold text-bg shadow-glow disabled:opacity-40"
            >
              {status?.hunt_status === 'running' ? 'Hunting…' : 'Run hunt'}
            </button>
          </>
        }
      />

      <SaveProgressBanner />

      <div className="px-6 pb-10">
        <motion.div
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          className="grid grid-cols-2 gap-3 sm:grid-cols-4"
        >
          <Stat label="Discovered" value={status?.jobs_count ?? 0} accent />
          <Stat
            label="Pending approval"
            value={status?.approvals_pending ?? 0}
            onClick={() => setShowApprovals((v) => !v)}
          />
          <Stat label="Applied" value={status?.applied_count ?? 0} />
          <Stat label="Applied today" value={status?.applied_today ?? 0} />
        </motion.div>

        {fetchMsg && (
          <p className={`mt-2 text-xs ${fetchMsg.warn ? 'text-warn' : 'text-muted'}`}>{fetchMsg.text}</p>
        )}

        {showApprovals && <ApprovalsPanel />}

        <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-4">
            <section className="glass rounded-xl2 p-4 shadow-card">
              <h2 className="mb-3 text-sm font-semibold">Pipeline</h2>
              <Kanban jobs={jobs} onSelect={setSelected} />
            </section>
            <SourcesPanel />
          </div>

          <div className="space-y-4">
            <AutonomyPanel />
            <div className="h-[460px]">
              <ReasoningFeed />
            </div>
          </div>
        </div>

        {!status?.has_profile && (
          <div className="glass mt-4 rounded-xl2 p-5 text-center text-sm text-muted">
            Build your profile first to start tailoring résumés →{' '}
            <a href="/onboarding" className="text-accent">Onboarding</a>
          </div>
        )}

        {!!status?.hunt_error && (
          <div className="glass mt-4 rounded-xl2 p-5 text-center text-sm text-warn">
            The last hunt failed: {status.hunt_error}
          </div>
        )}
      </div>

      <ResumePreview job={selected} onClose={() => setSelected(null)} />
    </main>
  );
}
