'use client';

import { useState } from 'react';
import { motion } from 'framer-motion';
import Nav from '@/components/Nav';
import AnimatedNumber from '@/components/AnimatedNumber';
import Kanban from '@/components/Kanban';
import ReasoningFeed from '@/components/ReasoningFeed';
import AutonomyPanel from '@/components/AutonomyPanel';
import ResumePreview from '@/components/ResumePreview';
import { api, Job } from '@/lib/api';
import { usePoll } from '@/lib/useLive';

function Stat({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="glass rounded-xl2 p-4 shadow-card">
      <div className={`text-3xl font-extrabold tabular-nums ${accent ? 'text-grad' : 'text-ink'}`}>
        <AnimatedNumber value={value} />
      </div>
      <div className="mt-1 text-xs text-muted">{label}</div>
    </div>
  );
}

export default function Dashboard() {
  const status = usePoll(() => api.status(), 2500);
  const jobsData = usePoll(() => api.jobs(), 2500);
  const [selected, setSelected] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
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

      <div className="px-6 pb-10">
        <motion.div
          initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
          className="grid grid-cols-2 gap-3 sm:grid-cols-4"
        >
          <Stat label="Discovered" value={status?.jobs_count ?? 0} accent />
          <Stat label="Pending approval" value={status?.approvals_pending ?? 0} />
          <Stat label="Applied" value={status?.applied_count ?? 0} />
          <Stat label="Applied today" value={status?.applied_today ?? 0} />
        </motion.div>

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
      </div>

      <ResumePreview job={selected} onClose={() => setSelected(null)} />
    </main>
  );
}
