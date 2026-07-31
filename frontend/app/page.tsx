'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Mark, Wordmark } from '@/components/Rail';
import { Tag, cx } from '@/components/ui';
import { joinWaitlist, recordPageview, type PricePref } from '@/lib/api';

// Three.js canvas is client-only — never SSR/prerender it.
const Hero3D = dynamic(() => import('@/components/Hero3D'), { ssr: false });

const NAV_LINKS = ['How it works', 'The seven agents', 'Safety', 'Pricing'];

// Static top-of-funnel figures — this is a pre-launch product (see /admin,
// which is honest that the waitlist is still at zero), so there is no live
// "postings read this week" endpoint to wire up. These match the reference
// mockup's demo numbers rather than claiming a real-time feed.
const STATS = [
  { value: '412', label: 'postings read this week' },
  { value: '88%', label: 'median ATS keyword coverage' },
  { value: '29', label: 'applications submitted for you' },
  { value: '5', label: 'interviews booked from your inbox' },
];

export default function Landing() {
  useEffect(() => { recordPageview('landing'); }, []);

  return (
    <main className="min-h-screen">
      <div className="flex items-center gap-6 px-6 py-5 sm:px-10">
        <Link href="/" className="mr-auto flex items-center gap-2.5 no-underline" style={{ color: 'inherit' }}>
          <Mark size={30} />
          <Wordmark size={23} />
        </Link>
        {NAV_LINKS.map((l) => (
          <span key={l} className="hidden text-sm text-muted sm:inline">{l}</span>
        ))}
        <button type="button" className="btn btn-secondary">Sign in</button>
        <Link href="/onboarding" className="btn btn-primary">Start free</Link>
      </div>

      <section className="grid items-center gap-10 px-6 py-10 sm:px-10 lg:grid-cols-[minmax(0,1fr)_min(600px,44vw)] lg:py-16">
        <div className="max-w-xl">
          <Tag tone="accent-2">Seven agents · one hunt</Tag>
          <h1 className="mt-4 text-[42px] leading-[0.98] sm:text-[64px]">
            Your job hunt,
            <br />
            running while
            <br />
            <span style={{ color: 'var(--color-accent)' }}>you sleep.</span>
          </h1>
          <p className="mt-5 max-w-md text-[17px] leading-relaxed text-muted">
            JobHunt reads the boards, throws out the ghost postings, writes a résumé it can defend
            line by line, applies for you, and tells you when someone writes back.
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link
              href="/onboarding"
              className="btn btn-primary anim-pulse"
              style={{ fontSize: 15, padding: '12px 24px' }}
            >
              Build my profile
            </Link>
            <Link href="/dashboard" className="btn btn-secondary" style={{ fontSize: 15, padding: '12px 24px' }}>
              See a live hunt
            </Link>
          </div>
          <div className="mt-6 flex items-center gap-2.5 text-[13px] text-muted">
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: 'var(--color-accent-2)' }} />
            Every bullet traced to your real experience. No invented skills, ever.
          </div>
        </div>

        <div className="relative hidden h-[420px] lg:block">
          <div
            className="absolute left-1/2 top-1/2 h-[300px] w-[300px] -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{ background: 'var(--color-surface)', filter: 'blur(2px)' }}
          />
          <Hero3D />
        </div>
      </section>

      <div
        className="grid grid-cols-2 gap-6 border-t px-6 py-6 sm:grid-cols-4 sm:px-10"
        style={{ borderColor: 'var(--color-divider)' }}
      >
        {STATS.map((s) => (
          <div key={s.label}>
            <div style={{ fontFamily: 'var(--font-heading)', fontSize: 36, lineHeight: 1.1 }}>{s.value}</div>
            <div className="text-[12.5px] text-muted">{s.label}</div>
          </div>
        ))}
      </div>

      <div className="px-6 pb-16 sm:px-10">
        <WaitlistForm />
      </div>
    </main>
  );
}

const PRICE_OPTIONS: { v: PricePref; label: string }[] = [
  { v: 'monthly_19', label: '$19/mo' },
  { v: 'monthly_29', label: '$29/mo' },
  { v: 'lifetime_99', label: '$99 lifetime' },
  { v: 'lifetime_149', label: '$149 lifetime' },
];

function WaitlistForm() {
  const [email, setEmail] = useState('');
  const [pref, setPref] = useState<PricePref>('lifetime_99');
  const [status, setStatus] = useState<'idle' | 'busy' | 'done' | 'error'>('idle');

  const submit = async () => {
    if (!email.includes('@')) return;
    setStatus('busy');
    try {
      await joinWaitlist(email, pref);
      setStatus('done');
    } catch {
      // A silently-dropped signup is worse than a visible failure: the user
      // walks away believing they're on the list, and the pricing data this
      // form exists to collect is quietly wrong.
      setStatus('error');
    }
  };

  if (status === 'done') {
    return <p className="text-sm text-muted">You're on the list — thanks.</p>;
  }

  return (
    <div className="card elev-sm w-full max-w-md p-5">
      <h3 className="text-base font-semibold">Want early access?</h3>
      <p className="mt-1 text-xs text-muted">Pick what you'd actually pay — helps us price it right.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {PRICE_OPTIONS.map((o) => (
          <button
            key={o.v}
            type="button"
            onClick={() => setPref(o.v)}
            className={cx('tag', pref === o.v ? 'tag-solid' : 'tag-neutral')}
          >
            {o.label}
          </button>
        ))}
      </div>
      <div className="mt-3 flex gap-2">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          className="input"
        />
        <button onClick={submit} disabled={status === 'busy'} className="btn btn-primary shrink-0">
          Join
        </button>
      </div>
      {status === 'error' && (
        <p className="mt-2 text-xs text-warn">
          That didn&apos;t go through — please try again.
        </p>
      )}
    </div>
  );
}
