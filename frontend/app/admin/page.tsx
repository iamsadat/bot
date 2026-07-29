'use client';

// Founder-facing validation dashboard. Deliberately NOT in the main Nav —
// it's a private surface reached by URL, gated on JOBHUNT_ADMIN_TOKEN.
//
// The token lives in component state only: never localStorage, never a
// cookie, never a query param. Closing the tab drops it. Re-typing it on
// each visit is a fair price for not leaving a long-lived admin credential
// sitting in browser storage.

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  billingStatus,
  getAdminPageviewStats,
  getAdminWaitlistStats,
  PageviewStats,
  PricePref,
  SurfaceStats,
  WaitlistStats,
} from '@/lib/api';

const PRICE_LABELS: { v: PricePref; label: string }[] = [
  { v: 'monthly_19', label: '$19/mo' },
  { v: 'monthly_29', label: '$29/mo' },
  { v: 'lifetime_99', label: '$99 lifetime' },
  { v: 'lifetime_149', label: '$149 lifetime' },
];

const SURFACES: { key: keyof PageviewStats; label: string }[] = [
  { key: 'landing', label: 'Landing page' },
  { key: 'ats_tool', label: 'Free ATS-score tool' },
  { key: 'public_resume', label: 'Public résumé pages' },
];

function Section({ title, hint, children }: {
  title: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <section className="glass rounded-xl2 p-4 shadow-card">
      <h2 className="text-sm font-semibold">{title}</h2>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** Horizontal share bar — no chart library, the data is four buckets wide. */
function Bar({ label, count, total }: { label: string; count: number; total: number }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-28 shrink-0 text-muted">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/5">
        <div className="h-full rounded-full bg-grad" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-16 shrink-0 text-right tabular-nums">
        {count} <span className="text-xs text-muted">({pct}%)</span>
      </span>
    </div>
  );
}

/** Sparkline-ish day column chart built from the by_day map. */
function DaySeries({ byDay }: { byDay: Record<string, number> }) {
  const days = Object.keys(byDay).sort();
  if (days.length === 0) return <p className="text-xs text-muted">No views yet.</p>;
  const peak = Math.max(...days.map((d) => byDay[d]));
  return (
    <div className="flex h-16 items-end gap-1">
      {days.map((d) => (
        <div
          key={d}
          title={`${d}: ${byDay[d]}`}
          className="min-w-[3px] flex-1 rounded-t bg-accent/60"
          style={{ height: `${Math.max(4, (byDay[d] / peak) * 100)}%` }}
        />
      ))}
    </div>
  );
}

function SurfaceCard({ label, stats }: { label: string; stats?: SurfaceStats }) {
  return (
    <div className="glass rounded-xl2 p-4 shadow-card">
      <div className="text-3xl font-extrabold tabular-nums text-ink">
        {stats?.total ?? 0}
      </div>
      <div className="mt-1 text-xs text-muted">{label}</div>
      <div className="mt-3"><DaySeries byDay={stats?.by_day ?? {}} /></div>
      {!!stats?.top_refs?.length && (
        <ul className="mt-3 space-y-1 text-xs text-muted">
          {stats.top_refs.slice(0, 5).map((r) => (
            <li key={r.ref} className="flex justify-between gap-2">
              <span className="truncate">/p/{r.ref}</span>
              <span className="tabular-nums">{r.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Admin() {
  const [token, setToken] = useState('');
  const [authed, setAuthed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [waitlist, setWaitlist] = useState<WaitlistStats | null>(null);
  const [pageviews, setPageviews] = useState<PageviewStats | null>(null);
  const [billing, setBilling] = useState<{ billing_configured: boolean } | null>(null);

  const load = useCallback(async (t: string) => {
    setError(null);
    try {
      const [w, p] = await Promise.all([
        getAdminWaitlistStats(t),
        getAdminPageviewStats(t),
      ]);
      setWaitlist(w);
      setPageviews(p);
      setAuthed(true);
    } catch (e) {
      setAuthed(false);
      setError(
        (e as Error).message === 'forbidden'
          ? 'Rejected. Check JOBHUNT_ADMIN_TOKEN on the server matches this token.'
          : 'Could not reach the API.',
      );
    }
  }, []);

  // Billing config is workspace-scoped and already public — no token needed.
  useEffect(() => { billingStatus().then(setBilling).catch(() => {}); }, []);

  if (!authed) {
    return (
      <main className="relative z-10 mx-auto grid min-h-screen max-w-md place-items-center px-6">
        <div className="glass w-full rounded-xl2 p-5 shadow-card">
          <h1 className="font-semibold">Validation dashboard</h1>
          <p className="mt-1 text-xs text-muted">
            Enter the admin token (server env <code>JOBHUNT_ADMIN_TOKEN</code>).
          </p>
          <form
            className="mt-3 flex gap-2"
            onSubmit={(e) => { e.preventDefault(); load(token); }}
          >
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="admin token"
              className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-ink"
            />
            <button
              type="submit"
              className="shrink-0 rounded-lg bg-grad px-4 py-2 text-sm font-semibold text-bg"
            >
              View
            </button>
          </form>
          {error && <p className="mt-3 text-xs text-warn">{error}</p>}
        </div>
      </main>
    );
  }

  const total = waitlist?.total ?? 0;

  return (
    <main className="relative z-10 mx-auto min-h-screen max-w-5xl px-6 py-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-extrabold tracking-tight">Validation dashboard</h1>
          <p className="text-xs text-muted">
            Is anyone showing up, and would any of them pay?
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => load(token)}
            className="glass rounded-full px-4 py-1.5 text-sm text-muted hover:text-ink"
          >
            Refresh
          </button>
          <Link href="/" className="text-sm text-muted hover:text-ink">← Site</Link>
        </div>
      </header>

      <div className="space-y-4">
        <Section
          title="Waitlist — stated willingness to pay"
          hint="What people picked when asked what they'd actually pay. A stated preference is not a sale, but zero signups is a clear answer."
        >
          <div className="mb-4 text-4xl font-extrabold tabular-nums text-grad">
            {total}
            <span className="ml-2 text-sm font-normal text-muted">
              signup{total === 1 ? '' : 's'}
            </span>
          </div>
          {total === 0 ? (
            <p className="text-sm text-muted">
              Nobody has signed up yet. Until this moves, there's no pricing signal
              to act on — the next step is traffic, not more features.
            </p>
          ) : (
            <div className="space-y-2">
              {PRICE_LABELS.map((o) => (
                <Bar
                  key={o.v}
                  label={o.label}
                  count={waitlist?.by_price_pref?.[o.v] ?? 0}
                  total={total}
                />
              ))}
            </div>
          )}
        </Section>

        <Section
          title="Top-of-funnel traffic"
          hint="Coarse day-level counts only — no IPs, user agents, or precise timestamps are stored."
        >
          <div className="grid gap-3 sm:grid-cols-3">
            {SURFACES.map((s) => (
              <SurfaceCard key={s.key} label={s.label} stats={pageviews?.[s.key]} />
            ))}
          </div>
        </Section>

        <Section title="Billing">
          <p className="text-sm">
            Stripe is{' '}
            <span className={billing?.billing_configured ? 'text-good' : 'text-warn'}>
              {billing?.billing_configured ? 'configured' : 'not configured'}
            </span>
            .{' '}
            <span className="text-muted">
              Checkout rails exist but no price is live — that decision waits on the
              numbers above.
            </span>
          </p>
        </Section>
      </div>
    </main>
  );
}
