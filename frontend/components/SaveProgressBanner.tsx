'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Button, Input } from './ui';

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
    <div
      className="mx-6 mt-4 flex flex-wrap items-center gap-3 rounded-[26px] p-4 text-sm elev-sm"
      style={{ background: 'var(--color-surface)' }}
    >
      {sent ? (
        <>
          <span>
            Check <span className="font-semibold">{email}</span> for a sign-in link.
          </span>
          {devLink && (
            <a href={devLink} className="underline" style={{ color: 'var(--color-accent)' }}>
              dev link (no SMTP configured)
            </a>
          )}
        </>
      ) : (
        <>
          <span>Save your progress — verify your email to keep your workspace.</span>
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="max-w-[220px]"
          />
          <Button variant="primary" onClick={submit} disabled={busy}>
            Send link
          </Button>
        </>
      )}
      <Button variant="ghost" icon onClick={() => setDismissed(true)} className="ml-auto" aria-label="Dismiss">
        ✕
      </Button>
    </div>
  );
}
