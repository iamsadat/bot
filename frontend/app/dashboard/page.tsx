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
  const jobs = jobsData?.jobs || [];

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  };

  return (
    <main className="relative z-10 mx-auto min-h-screen max-w-7xl">
      <Nav
        right={
          <>
            <button
              onClick={() => run(api.discover)}
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

        {showApprovals && <ApprovalsPanel />}

        <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-4">
            <section className="glass rounded-xl2 p-4 shadow-card">
              <h2 className="mb-3 text-sm font-semibold">Pipeline</h2>
              <Kanban jobs={jobs} onSelect={setSelected} />
            </section>
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
