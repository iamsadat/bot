'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { api, ActivityEvent } from '@/lib/api';
import { useReasoningStream } from '@/lib/useLive';
import { Tag } from './ui';

// ponytail: the backend sends `decision` as free text, not a signed outcome —
// a reject/refuse keyword is the cheapest honest way to pick the accent vs
// olive conclusion colour the mock calls for without inventing a new field.
function decisionTone(decision: string): string {
  return /reject|refus/i.test(decision) ? 'var(--color-accent-700)' : 'var(--color-accent-2-700)';
}

// ponytail: no "which colour" signal exists per event either; alternating by
// index gives the same visual variety the mock has without faking meaning.
function dotColor(i: number): string {
  return i % 2 === 0 ? 'var(--color-accent)' : 'var(--color-accent-2)';
}

export default function ReasoningFeed() {
  const liveEvents = useReasoningStream();
  const [history, setHistory] = useState<ActivityEvent[]>([]);

  useEffect(() => {
    api.activity().then((d) => setHistory(d.activity.slice().reverse())).catch(() => {});
  }, []);

  const events = [...liveEvents, ...history];
  return (
    <div className="flex h-full flex-col gap-2.5">
      <div className="flex items-baseline gap-2 px-1">
        <h4 className="m-0 text-[16px]">Reasoning stream</h4>
        <span
          className="anim-blink h-[7px] w-[7px] rounded-full"
          style={{ background: 'var(--color-accent)' }}
        />
        <span className="ml-auto text-[11px] text-muted">live</span>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto">
        {events.length === 0 && (
          <p className="px-2 py-8 text-center text-xs text-muted">
            Run a hunt to watch the agents think…
          </p>
        )}
        <AnimatePresence initial={false}>
          {events.map((e: ActivityEvent, i) => (
            <motion.div
              key={`${e.task_id}-${i}-${e.thought.slice(0, 12)}`}
              initial={{ opacity: 0, x: -12, height: 0 }}
              animate={{ opacity: 1, x: 0, height: 'auto' }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="rounded-[24px] p-4"
              style={{ background: 'var(--color-surface)' }}
            >
              <div className="mb-1.5 flex flex-nowrap items-center gap-2">
                <span
                  className="h-[17px] w-[17px] flex-none rounded-full"
                  style={{ background: dotColor(i) }}
                />
                <span className="whitespace-nowrap text-[12px] font-bold">{e.agent}</span>
                {typeof e.confidence === 'number' && (
                  <span className="ml-auto whitespace-nowrap text-[11px] text-muted">
                    confidence {e.confidence.toFixed(2)}
                  </span>
                )}
              </div>
              <p className="text-[13px] leading-[1.5]">{e.thought}</p>
              {e.decision && (
                <p className="mt-1.5 text-[11.5px]" style={{ color: decisionTone(e.decision) }}>
                  {e.decision}
                </p>
              )}
              {!!e.considered?.length && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {e.considered.slice(0, 6).map((c) => (
                    <Tag key={c} tone="accent">{c}</Tag>
                  ))}
                </div>
              )}
              {!!e.rejected?.length && (
                <ul className="mt-1.5 space-y-0.5">
                  {e.rejected.slice(0, 4).map((r, j) => (
                    <li key={j} className="text-[11px]" style={{ color: 'var(--color-accent-700)' }}>
                      ✕ {r.item} <span className="text-muted">— {r.reason}</span>
                    </li>
                  ))}
                </ul>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
