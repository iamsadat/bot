'use client';

import { AnimatePresence, motion } from 'framer-motion';
import { ActivityEvent } from '@/lib/api';
import { useReasoningStream } from '@/lib/useLive';

const phaseColor: Record<string, string> = {
  deliberate: 'text-accent',
  act: 'text-ink',
  critique: 'text-warn',
  decide: 'text-good',
};

function Confidence({ v }: { v: number }) {
  return (
    <span className="ml-auto inline-flex items-center gap-1 text-[10px] text-muted">
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-white/10">
        <span
          className="block h-full rounded-full bg-grad"
          style={{ width: `${Math.round(v * 100)}%` }}
        />
      </span>
      {Math.round(v * 100)}%
    </span>
  );
}

export default function ReasoningFeed() {
  const events = useReasoningStream();
  return (
    <div className="glass flex h-full flex-col rounded-xl2 shadow-card">
      <div className="flex items-center gap-2 border-b border-white/5 px-4 py-3">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-good opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-good" />
        </span>
        <h3 className="text-sm font-semibold">Live agent reasoning</h3>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3">
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
              className="rounded-lg border border-white/5 bg-white/[0.02] p-2.5"
            >
              <div className="flex items-center gap-2 text-[11px]">
                <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-muted">
                  {e.agent}
                </span>
                {e.phase && (
                  <span className={`font-medium ${phaseColor[e.phase] || 'text-muted'}`}>
                    {e.phase}
                  </span>
                )}
                {typeof e.confidence === 'number' && <Confidence v={e.confidence} />}
              </div>
              <p className="mt-1 text-[13px] leading-snug text-ink/90">{e.thought}</p>
              {!!e.considered?.length && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {e.considered.slice(0, 6).map((c) => (
                    <span
                      key={c}
                      className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}
              {!!e.rejected?.length && (
                <ul className="mt-1.5 space-y-0.5">
                  {e.rejected.slice(0, 4).map((r, j) => (
                    <li key={j} className="text-[11px] text-bad/80">
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
