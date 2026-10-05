'use client';

/* "Ask JobHunt" — the in-app assistant, mounted once in the root layout.
 *
 * A floating button opens a slide-over chat. The server is stateless: every
 * turn sends the visible history (it keeps the tail). The reply can carry
 * changes it already made (`applied`) and changes that need a yes first
 * (`pending`), which render as cards with Confirm / Dismiss. Whenever
 * something changed, a `jobhunt:refresh` event tells the open page to
 * re-fetch.
 */

import { useEffect, useRef, useState } from 'react';
import { Button, Card, Tag } from '@/components/ui';
import { api, AssistantAction, AssistantApplied, ChatMessage } from '@/lib/api';

type Turn = {
  role: 'user' | 'assistant';
  content: string;
  applied?: AssistantApplied[];
  pending?: (AssistantAction & { state?: 'confirmed' | 'dismissed' })[];
  error?: boolean;
};

const STORAGE_KEY = 'jh_assistant_chat';

const STARTERS = [
  'Fill my screening answers: notice period 30 days, expected CTC 12 LPA',
  'Why is my top job a 82% match?',
  'Mark Amazon as Interview',
  'Add Airflow and dbt to my skills',
];

function loadTurns(): Turn[] {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    const v = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

function saveTurns(turns: Turn[]): void {
  try { window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(turns.slice(-40))); } catch { /* storage blocked */ }
}

const history = (turns: Turn[]): ChatMessage[] =>
  turns.filter((t) => !t.error && t.content).map((t) => ({ role: t.role, content: t.content }));

function announceRefresh(applied: AssistantApplied[]) {
  if (applied.some((a) => a.ok && a.action !== 'explain_match')) {
    window.dispatchEvent(new Event('jobhunt:refresh'));
  }
}

export default function AssistantPanel() {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const loaded = useRef(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setTurns(loadTurns()); loaded.current = true; }, []);
  useEffect(() => { if (loaded.current) saveTurns(turns); }, [turns]);
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }); }, [turns, busy, open]);
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const call = async (base: Turn[], confirm?: AssistantAction[]) => {
    setBusy(true);
    try {
      const r = await api.assistantChat(history(base), confirm);
      setTurns((t) => [...t, { role: 'assistant', content: r.reply, applied: r.applied, pending: r.pending }]);
      announceRefresh(r.applied);
    } catch (e) {
      setTurns((t) => [...t, {
        role: 'assistant', error: true,
        content: e instanceof Error ? e.message : 'Something went wrong — try again.',
      }]);
    } finally {
      setBusy(false);
    }
  };

  const send = (raw: string) => {
    const content = raw.trim();
    if (!content || busy) return;
    const next = [...turns, { role: 'user' as const, content }];
    setTurns(next);
    setText('');
    void call(next);
  };

  const decide = (turnIdx: number, actionIdx: number, ok: boolean) => {
    const action = turns[turnIdx]?.pending?.[actionIdx];
    if (!action || action.state || busy) return;
    const next = turns.map((t, i) => i !== turnIdx ? t : {
      ...t,
      pending: t.pending?.map((p, j) => j === actionIdx ? { ...p, state: ok ? 'confirmed' as const : 'dismissed' as const } : p),
    });
    setTurns(next);
    if (ok) {
      const { name, args } = action;
      void call(next, [{ name, args }]);
    }
  };

  return (
    <>
      {!open && (
        <Button
          variant="primary"
          className="fixed z-30 elev-lg"
          style={{ right: 16, bottom: 'max(16px, env(safe-area-inset-bottom))' }}
          onClick={() => setOpen(true)}
          aria-haspopup="dialog"
        >
          <span aria-hidden>✦</span> Ask JobHunt
        </Button>
      )}

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            style={{ background: 'color-mix(in srgb, var(--color-neutral-900) 35%, transparent)' }}
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <aside
            role="dialog"
            aria-label="Ask JobHunt"
            className="anim-rise fixed bottom-0 right-0 top-0 z-50 flex w-full max-w-[420px] flex-col elev-lg"
            style={{ background: 'var(--color-bg)' }}
          >
            <header
              className="flex items-center gap-2 px-4 py-3"
              style={{ borderBottom: '1px solid var(--color-divider)' }}
            >
              <div className="min-w-0 flex-1">
                <div className="card-title">Ask JobHunt</div>
                <div className="text-xs text-muted">Ask about your search, or tell it what to change.</div>
              </div>
              {turns.length > 0 && (
                <Button variant="ghost" onClick={() => setTurns([])} disabled={busy}>Clear</Button>
              )}
              <Button icon aria-label="Close" onClick={() => setOpen(false)}>✕</Button>
            </header>

            <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {turns.length === 0 && (
                <div className="space-y-2">
                  <div className="text-sm text-muted">Try one of these:</div>
                  {STARTERS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className="card lift block w-full cursor-pointer text-left text-sm"
                      style={{ border: 0, font: 'inherit', color: 'inherit' }}
                      onClick={() => send(s)}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}

              {turns.map((t, i) => (
                <div key={i} className={t.role === 'user' ? 'flex justify-end' : 'flex flex-col gap-2'}>
                  <div
                    className="max-w-[88%] whitespace-pre-wrap break-words px-3 py-2 text-sm"
                    style={{
                      borderRadius: 'var(--radius-md)',
                      background: t.role === 'user' ? 'var(--color-accent-100)' : 'var(--color-surface)',
                      color: t.error ? 'var(--color-accent-700)' : undefined,
                    }}
                  >
                    {t.content || (t.role === 'assistant' ? '…' : '')}
                  </div>

                  {t.applied && t.applied.length > 0 && (
                    <ul className="m-0 list-none space-y-1 p-0 text-xs">
                      {t.applied.map((a, j) => (
                        <li key={j} className="flex gap-2">
                          <span
                            aria-label={a.ok ? 'done' : 'failed'}
                            style={{ color: a.ok ? 'var(--color-accent-2-700)' : 'var(--color-accent-700)', fontWeight: 700 }}
                          >
                            {a.ok ? '✓' : '✗'}
                          </span>
                          <span className="min-w-0 break-words">{a.detail}</span>
                        </li>
                      ))}
                    </ul>
                  )}

                  {t.pending?.map((p, j) => (
                    <Card key={j} elevation="sm" className="text-sm">
                      <div className="flex items-center gap-2">
                        <Tag tone="accent">Needs your OK</Tag>
                        {p.state && <Tag tone="neutral">{p.state === 'confirmed' ? 'Confirmed' : 'Dismissed'}</Tag>}
                      </div>
                      <div>{p.summary || p.name}</div>
                      {!p.state && (
                        <div className="flex gap-2">
                          <Button variant="primary" onClick={() => decide(i, j, true)} disabled={busy}>Confirm</Button>
                          <Button onClick={() => decide(i, j, false)} disabled={busy}>Dismiss</Button>
                        </div>
                      )}
                    </Card>
                  ))}
                </div>
              ))}

              {busy && <div className="text-sm text-muted">thinking<span className="anim-blink">…</span></div>}
              <div ref={endRef} />
            </div>

            <form
              className="flex items-end gap-2 px-4 py-3"
              style={{ borderTop: '1px solid var(--color-divider)', paddingBottom: 'max(12px, env(safe-area-inset-bottom))' }}
              onSubmit={(e) => { e.preventDefault(); send(text); }}
            >
              {/* Plain element (the Textarea primitive does not forward refs); same .input skin. */}
              <textarea
                ref={inputRef}
                rows={1}
                value={text}
                placeholder="Ask or tell JobHunt…"
                aria-label="Message"
                className="input flex-1"
                style={{ minHeight: 40, maxHeight: 140, borderRadius: 'var(--radius-md)' }}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    send(text);
                  }
                }}
              />
              <Button type="submit" variant="primary" disabled={busy || !text.trim()}>Send</Button>
            </form>
          </aside>
        </>
      )}
    </>
  );
}
