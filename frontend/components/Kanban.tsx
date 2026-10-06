'use client';

import { motion } from 'framer-motion';
import { Job } from '@/lib/api';
import { Tag, Button } from './ui';
import { band } from './ResumePreview';

const COLUMNS = ['Saved', 'Applied', 'Assessment', 'Interview', 'Offer', 'Closed'];

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
          <div key={col} className="min-w-0">
            <div className="mb-2 flex items-center justify-between px-1">
              <span className="text-[11px] uppercase tracking-[0.08em] text-muted">{col}</span>
              <span className="text-[11px] text-muted">{items.length}</span>
            </div>
            <div className="space-y-2">
              {items.map((j) => {
                const awaiting = !!j.awaiting_approval;
                return (
                  <motion.div
                    layout
                    key={j.job_id}
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelect(j)}
                    onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect(j)}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    // Every card gets its own surface: on --color-surface it
                    // matched the column behind it and only the highlighted
                    // (awaiting) card was visible as a card at all.
                    className="lift elev-sm w-full cursor-pointer rounded-[22px] border-[1.5px] p-3.5 text-left"
                    style={{
                      background: 'var(--color-bg)',
                      borderColor: awaiting ? 'var(--color-accent)' : 'transparent',
                    }}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <p className="truncate text-[13px] font-bold">{j.company}</p>
                      {j.url && (
                        <a href={j.url} target="_blank" rel="noreferrer" title="Open posting"
                           aria-label={`Open ${j.company} posting`}
                           onClick={(e) => e.stopPropagation()}
                           className="shrink-0 text-[12px] text-muted hover:opacity-70">↗</a>
                      )}
                    </div>
                    <p className="mb-2 truncate text-[11.5px] text-muted">{j.title}</p>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {typeof j.relevance_score === 'number' && j.relevance_score > 0 && (
                        <Tag tone={j.relevance_score >= 0.8 ? 'accent-2' : 'neutral'}>
                          {Math.round(j.relevance_score * 100)}% match
                        </Tag>
                      )}
                      {j.tier && <Tag tone="neutral">{j.tier}</Tag>}
                      {j.salary && <Tag tone="neutral">{band(j.salary.min, j.salary.max)}</Tag>}
                      {j.remote && <Tag tone="neutral">remote</Tag>}
                      {j.submitted && <Tag tone="accent-2">submitted</Tag>}
                      {j.needs_you && !j.submitted && (
                        <Tag tone="outline" title={j.needs_you}>needs you</Tag>
                      )}
                    </div>
                    {awaiting && (
                      <div className="mt-2.5 flex gap-1.5" onClick={(e) => e.stopPropagation()}>
                        <Button
                          variant="primary"
                          style={{ fontSize: 12, padding: '6px 13px' }}
                          onClick={() => onSelect(j)}
                        >
                          Approve
                        </Button>
                        <Button
                          variant="secondary"
                          style={{ fontSize: 12, padding: '6px 13px' }}
                          onClick={() => onSelect(j)}
                        >
                          Preview
                        </Button>
                      </div>
                    )}
                  </motion.div>
                );
              })}
              {items.length === 0 && (
                <p className="px-1 py-3 text-center text-[11px] text-muted opacity-60">—</p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
