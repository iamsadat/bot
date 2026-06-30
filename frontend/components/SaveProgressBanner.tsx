'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

// Minimal "save my progress" prompt: ties the anonymous jh_ws workspace
// cookie to a verified email via a magic link, so a user's résumés/
// applications/CRM data can follow them across devices or a cleared
// cookie jar. Backend: POST /api/auth/request-link, GET /api/auth/verify.
export default function SaveProgressBanner() {
  const [linkedEmail, setLinkedEmail] = useState<string | null | undefined>(undefined);
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [devLink, setDevLink] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.authStatus().then((r) => setLinkedEmail(r.linked_email)).catch(() => setLinkedEmail(null));
  }, []);

  if (linkedEmail === undefined || linkedEmail || dismissed) return null;

  const submit = async () => {
    if (!email.trim() || !email.includes('@')) return;
    setBusy(true);
    try {
      const r = await api.requestMagicLink(email.trim());
      setSent(true);
      setDevLink(r.dev_link || null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass mx-6 mt-4 flex flex-wrap items-center gap-3 rounded-xl2 p-4 text-sm shadow-card">
      {sent ? (
        <>
          <span className="text-ink">
            Check <span className="font-semibold">{email}</span> for a sign-in link.
          </span>
          {devLink && (
            <a href={devLink} className="text-grad underline">
              dev link (no SMTP configured)
            </a>
          )}
        </>
      ) : (
        <>
          <span className="text-ink">Save your progress — verify your email to keep your workspace.</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="glass rounded-full px-3 py-1.5 text-sm outline-none"
          />
          <button
            onClick={submit}
            disabled={busy}
            className="rounded-full bg-grad px-4 py-1.5 text-sm font-semibold text-bg disabled:opacity-40"
          >
            Send link
          </button>
        </>
      )}
      <button
        onClick={() => setDismissed(true)}
        className="ml-auto text-muted hover:text-ink"
        aria-label="Dismiss"
      >
        ✕
      </button>
    </div>
  );
}
