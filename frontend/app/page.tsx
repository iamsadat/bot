'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import { joinWaitlist, recordPageview, type PricePref } from '@/lib/api';

// Three.js canvas is client-only — never SSR/prerender it.
const Hero3D = dynamic(() => import('@/components/Hero3D'), { ssr: false });

const features = [
  { t: 'Evidence-backed tailoring', d: 'Every résumé bullet maps to your real experience — no invented skills.' },
  { t: 'Continuous + autonomous', d: 'Discovers and auto-applies to fresh matches around the clock, capped & safe.' },
  { t: 'Transparent reasoning', d: 'Watch each agent decide — what it considered, rejected, and why.' },
];

export default function Landing() {
  useEffect(() => { recordPageview('landing'); }, []);

  return (
    <main className="relative min-h-screen overflow-hidden">
      <div className="absolute inset-0 opacity-70">
        <Hero3D />
      </div>
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-transparent via-bg/40 to-bg" />

      <section className="relative z-10 mx-auto flex min-h-screen max-w-5xl flex-col items-center justify-center px-6 text-center">
        <motion.span
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="glass mb-6 rounded-full px-4 py-1.5 text-xs font-medium text-muted"
        >
          Open-source · self-hostable · $0 LLM tier
        </motion.span>

        <motion.h1
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.05 }}
          className="text-5xl font-extrabold leading-tight tracking-tight sm:text-7xl"
        >
          Your autonomous
          <br />
          <span className="text-grad">job-application copilot</span>
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.15 }}
          className="mt-6 max-w-2xl text-lg text-muted"
        >
          Multi-agent discovery, evidence-backed résumé tailoring, and real auto-submit —
          with a reasoning feed you can actually read.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.25 }}
          className="mt-10 flex flex-wrap items-center justify-center gap-4"
        >
          <Link
            href="/dashboard"
            className="rounded-full bg-grad px-7 py-3 font-semibold text-bg shadow-glow transition hover:scale-[1.03]"
          >
            Open dashboard →
          </Link>
          <Link
            href="/onboarding"
            className="glass rounded-full px-7 py-3 font-semibold text-ink transition hover:border-white/20"
          >
            Build my profile
          </Link>
        </motion.div>

        <div className="mt-20 grid w-full gap-4 sm:grid-cols-3">
          {features.map((f, i) => (
            <motion.div
              key={f.t}
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.35 + i * 0.1 }}
              className="glass rounded-xl2 p-5 text-left shadow-card"
            >
              <h3 className="font-semibold text-ink">{f.t}</h3>
              <p className="mt-2 text-sm text-muted">{f.d}</p>
            </motion.div>
          ))}
        </div>

        <WaitlistForm />
      </section>
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
  const [status, setStatus] = useState<'idle' | 'busy' | 'done'>('idle');

  const submit = async () => {
    if (!email.includes('@')) return;
    setStatus('busy');
    try {
      await joinWaitlist(email, pref);
      setStatus('done');
    } catch {
      setStatus('idle');
    }
  };

  if (status === 'done') {
    return <p className="mt-12 text-sm text-muted">You're on the list — thanks.</p>;
  }

  return (
    <div className="glass mt-16 w-full max-w-md rounded-xl2 p-5 text-left shadow-card">
      <h3 className="font-semibold text-ink">Want early access?</h3>
      <p className="mt-1 text-xs text-muted">Pick what you'd actually pay — helps us price it right.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {PRICE_OPTIONS.map((o) => (
          <button
            key={o.v}
            onClick={() => setPref(o.v)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition ${
              pref === o.v ? 'bg-grad text-bg' : 'glass text-muted hover:text-ink'
            }`}
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
          className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-ink"
        />
        <button
          onClick={submit}
          disabled={status === 'busy'}
          className="shrink-0 rounded-lg bg-grad px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
        >
          Join
        </button>
      </div>
    </div>
  );
}
