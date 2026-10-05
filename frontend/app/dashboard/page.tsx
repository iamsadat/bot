'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import Nav from '@/components/Nav';
import Rail from '@/components/Rail';
import SaveProgressBanner from '@/components/SaveProgressBanner';
import AnimatedNumber from '@/components/AnimatedNumber';
import Kanban from '@/components/Kanban';
import ReasoningFeed from '@/components/ReasoningFeed';
import AutonomyPanel from '@/components/AutonomyPanel';
import ResumePreview from '@/components/ResumePreview';
import { Button, Card, CardKicker, PageHead, SectionHead, Stat } from '@/components/ui';
import { api, Approval, Capability, describeSubmission, Job } from '@/lib/api';
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

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning.';
  if (h < 18) return 'Good afternoon.';
  return 'Good evening.';
}

function SourcesPanel() {
  const sourcesData = usePoll(() => api.sources(), 5000);
  const sources = sourcesData?.sources || [];

  return (
    <Card elevation="sm" className="mt-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Sources</h2>
        {sourcesData && <span className="text-xs text-muted">page {sourcesData.page}</span>}
      </div>
      {sources.length === 0 ? (
        <p className="text-xs text-muted">Per-source results show after the next sweep (Fetch jobs runs one now).</p>
      ) : (
        <div className="space-y-2">
          {sources.map((s) => (
            <div
              key={s.name}
              className="flex items-center justify-between rounded-xl2 p-2.5"
              style={{ background: 'var(--color-bg)' }}
            >
              <div className="flex min-w-0 items-center gap-2">
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
      {sourcesData?.seeded_boards && (
        <p className="mt-1 text-xs text-muted">
          Searching {sourcesData.seeded_board_count} curated public company boards.{' '}
          <Link href="/onboarding#boards" style={{ color: 'var(--color-accent)' }}>
            Add your own employers
          </Link>{' '}
          to search them too.
        </p>
      )}
    </Card>
  );
}

function ApprovalsPanel() {
  const approvalsData = usePoll(() => api.approvals(), 2500);
  const [busyId, setBusyId] = useState<string | null>(null);
  // The approved row leaves the list on the next poll, so what the approve
  // actually did is reported at panel level, not on the row.
  const [note, setNote] = useState<{ text: string; warn?: boolean } | null>(null);
  const approvals = approvalsData?.approvals || [];

  const decide = async (a: Approval, decision: 'approve' | 'reject') => {
    setBusyId(a.request_id);
    setNote(null);
    try {
      const r = await api.approve(a.request_id, decision);
      const s = decision === 'approve' ? describeSubmission(r.submission) : null;
      if (s) setNote({ ...s, text: `${a.company}: ${s.text}` });
    } catch (e) {
      setNote({ text: e instanceof Error ? e.message : String(e), warn: true });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
      <Card elevation="sm">
        <h2 className="text-sm font-semibold">Pending approval</h2>
        {approvals.length === 0 ? (
          <p className="text-xs text-muted">Nothing waiting on you.</p>
        ) : (
          <div className="space-y-2">
            {approvals.map((a) => (
              <div
                key={a.request_id}
                className="flex items-center justify-between rounded-xl2 p-2.5"
                style={{ background: 'var(--color-bg)' }}
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{a.title}</div>
                  <div className="truncate text-xs text-muted">{a.company}</div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button variant="primary" onClick={() => decide(a, 'approve')} disabled={busyId === a.request_id}>
                    Approve
                  </Button>
                  <Button variant="secondary" onClick={() => decide(a, 'reject')} disabled={busyId === a.request_id}>
                    Reject
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
        {note && <p className={`text-xs ${note.warn ? 'text-warn' : 'text-muted'}`}>{note.text}</p>}
      </Card>
    </motion.div>
  );
}

// What this install has switched on. Hidden when the server doesn't report it.
function SetupPanel({ capabilities }: { capabilities?: Capability[] }) {
  if (!Array.isArray(capabilities) || capabilities.length === 0) return null;
  const onCount = capabilities.filter((c) => c.on).length;
  return (
    <Card elevation="sm">
      <div className="flex items-center justify-between">
        <CardKicker>Your setup</CardKicker>
        <span className="text-[10px] text-muted">{onCount} of {capabilities.length} on</span>
      </div>
      <div className="flex flex-col gap-1.5">
        {capabilities.map((c) => (
          <div key={c.key} className="flex items-start gap-2 text-[12.5px]">
            <span
              className="rail-dot mt-[6px]"
              style={{ background: c.on ? 'var(--color-accent-2)' : 'var(--color-neutral-400)' }}
              aria-hidden
            />
            <div className="min-w-0">
              <div className={c.on ? undefined : 'text-muted'}>
                {c.label}
                <span className="sr-only">{c.on ? ' (on)' : ' (off)'}</span>
              </div>
              {!c.on && c.hint && (
                <div className="text-[11px] leading-snug text-muted">{c.hint}</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function Dashboard() {
  const router = useRouter();
  const status = usePoll(() => api.status(), 2500);
  const jobsData = usePoll(() => api.jobs(), 2500);
  const [selected, setSelected] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [showApprovals, setShowApprovals] = useState(false);
  const [fetchMsg, setFetchMsg] = useState<{ text: string; warn?: boolean } | null>(null);
  const jobs = jobsData?.jobs || [];

  // Nothing on this page works without a profile, so send a first run straight
  // to building one. Only an explicit `false` counts — a failed poll is not "no profile".
  const hasProfile = status?.has_profile;
  useEffect(() => {
    if (hasProfile === false) router.replace('/onboarding');
  }, [hasProfile, router]);

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

  const syncInbox = async () => {
    setFetchMsg(null);
    setBusy(true);
    try {
      const r = await api.syncInbox();
      if (r.ok === false) {
        setFetchMsg({ text: `Inbox sync failed${r.error ? `: ${r.error}` : '.'}`, warn: true });
      } else {
        const checked = r.checked ?? 0;
        const updates = r.updates ?? 0;
        setFetchMsg({
          text: `Inbox synced · ${checked} new message${checked === 1 ? '' : 's'} checked · `
            + `${updates} application${updates === 1 ? '' : 's'} updated`,
        });
      }
    } catch (e) {
      setFetchMsg({ text: e instanceof Error ? e.message : String(e), warn: true });
    } finally {
      setBusy(false);
    }
  };

  // Neither figure has its own API field — both are derived client-side from
  // the already-polled `jobs` list, so no new network calls are introduced.
  const vettedCount = jobs.filter((j) => typeof j.relevance_score === 'number' && j.relevance_score > 0).length;
  const interviewCount = jobs.filter((j) => j.status === 'Interview').length;

  const actions = (
    <>
      {status?.inbox_connected && (
        <Button variant="secondary" onClick={syncInbox} disabled={busy}>
          Sync inbox
        </Button>
      )}
      <Button variant="secondary" onClick={fetchMore} disabled={busy || !status?.has_profile}>
        Fetch jobs
      </Button>
      <Button
        variant="primary"
        className="anim-pulse"
        onClick={() => run(api.startHunt)}
        disabled={busy || !status?.has_profile}
      >
        {status?.hunt_status === 'running' ? 'Hunting…' : 'Run a hunt'}
      </Button>
    </>
  );

  return (
    <>
      <Rail />
      <div className="lg:pl-[220px]">
        <div className="lg:hidden">
          <Nav right={actions} />
        </div>

        <main className="relative z-10 mx-auto min-h-screen max-w-7xl">
          {/* Ties an anonymous workspace to an email — meaningless on a
              single-user install, where it could never be satisfied. */}
          {status && !status.personal && <SaveProgressBanner />}

          <div className="space-y-5 px-6 pb-10 pt-6">
            <PageHead
              kicker={`${new Date().toLocaleDateString(undefined, { weekday: 'long' })} · ${
                status?.hunt_status === 'running' ? 'hunting now' : status?.hunt_status ?? 'idle'
              }`}
              title={greeting()}
              subtitle={
                <>
                  {status?.approvals_pending
                    ? `${status.approvals_pending} résumé${status.approvals_pending === 1 ? '' : 's'} waiting on your approval.`
                    : 'All caught up — nothing waiting on you right now.'}
                  {!!status?.approvals_pending && (
                    <button
                      type="button"
                      onClick={() => setShowApprovals((v) => !v)}
                      className="ml-2 font-semibold"
                      style={{ color: 'var(--color-accent)' }}
                    >
                      Review now →
                    </button>
                  )}
                </>
              }
              actions={<div className="hidden items-center gap-3 lg:flex">{actions}</div>}
            />

            <motion.div
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
              className="grid grid-cols-2 gap-3 sm:grid-cols-4"
            >
              <Stat label="Discovered" value={<AnimatedNumber value={status?.jobs_count ?? 0} />} />
              <Stat
                label="Vetted in"
                value={<AnimatedNumber value={vettedCount} />}
                note={`of ${status?.jobs_count ?? 0}`}
              />
              <Stat
                label="Applied"
                value={<AnimatedNumber value={status?.applied_count ?? 0} />}
                note={status?.applied_today ? `+${status.applied_today} today` : undefined}
                noteTone="good"
              />
              <Stat label="Interviews" value={<AnimatedNumber value={interviewCount} />} accent />
            </motion.div>

            {fetchMsg && (
              <p className={`text-xs ${fetchMsg.warn ? 'text-warn' : 'text-muted'}`}>{fetchMsg.text}</p>
            )}

            {showApprovals && <ApprovalsPanel />}

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
              <div className="min-w-0 space-y-4">
                <Card elevation="sm">
                  <SectionHead title="Pipeline" />
                  <Kanban jobs={jobs} onSelect={setSelected} />
                </Card>
                <SourcesPanel />
              </div>

              <div className="space-y-4">
                <AutonomyPanel />
                <SetupPanel capabilities={status?.capabilities} />
                <div className="h-[460px]">
                  <ReasoningFeed />
                </div>
              </div>
            </div>

            {!status?.has_profile && (
              <Card elevation="sm" className="text-center text-sm text-muted">
                Build your profile first to start tailoring résumés →{' '}
                <a href="/onboarding" style={{ color: 'var(--color-accent)' }}>Onboarding</a>
              </Card>
            )}

            {!!status?.hunt_error && (
              <Card elevation="sm" className="text-center text-sm text-warn">
                The last hunt failed: {status.hunt_error}
              </Card>
            )}
          </div>

          <ResumePreview job={selected} onClose={() => setSelected(null)} />
        </main>
      </div>
    </>
  );
}
