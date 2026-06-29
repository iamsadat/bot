'use client';

import { motion } from 'framer-motion';
import { Job } from '@/lib/api';

const COLUMNS = ['Saved', 'Applied', 'Assessment', 'Interview', 'Offer', 'Closed'];

const dot: Record<string, string> = {
  Saved: 'bg-muted', Applied: 'bg-accent', Assessment: 'bg-warn',
  Interview: 'bg-accent2', Offer: 'bg-good', Closed: 'bg-bad',
};

export default function Kanban({
  jobs, onSelect,
}: {
  jobs: Job[];
  onSelect: (j: Job) => void;
}) {
  return (
    <div className="grid auto-cols-[minmax(220px,1fr)] grid-flow-col gap-3 overflow-x-auto pb-2">
      {COLUMNS.map((col) => {
        const items = jobs.filter((j) => (j.status || 'Saved') === col);
        return (
          <div key={col} className="glass rounded-xl2 p-3 shadow-card">
            <div className="mb-2 flex items-center justify-between px-1">
              <span className="flex items-center gap-2 text-xs font-semibold">
                <span className={`h-2 w-2 rounded-full ${dot[col]}`} />
                {col}
              </span>
              <span className="text-[11px] text-muted">{items.length}</span>
            </div>
            <div className="space-y-2">
              {items.map((j) => (
                <motion.button
                  layout
                  key={j.job_id}
                  onClick={() => onSelect(j)}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  whileHover={{ y: -2 }}
                  className="w-full rounded-lg border border-white/8 bg-white/[0.02] p-2.5 text-left transition hover:border-white/20"
                >
                  <p className="truncate text-[13px] font-medium text-ink">{j.title}</p>
                  <p className="truncate text-[11px] text-muted">{j.company}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1">
                    {typeof j.relevance_score === 'number' && j.relevance_score > 0 && (
                      <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                        {Math.round(j.relevance_score * 100)}% match
                      </span>
                    )}
                    {j.remote && (
                      <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-muted">remote</span>
                    )}
                    {j.submitted && (
                      <span className="rounded bg-good/10 px-1.5 py-0.5 text-[10px] text-good">submitted</span>
                    )}
                  </div>
                </motion.button>
              ))}
              {items.length === 0 && (
                <p className="px-1 py-3 text-center text-[11px] text-muted/60">—</p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
