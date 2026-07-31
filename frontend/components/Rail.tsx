'use client';

/* The Grove app shell: a fixed 220px rail replacing the old top-bar Nav.
 *
 * Route mapping note — the mockup's rail lists Pipeline, Agents, Applications,
 * Profile & résumé, Tracker and Phone app. Agents and Applications are not
 * routes in this app; they are regions of /dashboard (ReasoningFeed and
 * Kanban). Rather than invent two empty pages, they are in-page anchors, and
 * the rail carries the routes that actually exist.
 */

import Link from 'next/link';
import { usePathname } from 'next/navigation';

export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 26 26" fill="none" aria-hidden>
      <circle cx="13" cy="13" r="11.2" stroke="var(--color-accent)" strokeWidth="2.75" />
      <path d="M13 5.6 L16.6 15.4 L13 13.2 L9.4 15.4 Z" fill="var(--color-accent-2)" />
      <circle cx="13" cy="13" r="2.1" fill="var(--color-accent)" />
    </svg>
  );
}

export function Wordmark({ size = 21 }: { size?: number }) {
  return (
    <span
      style={{ fontFamily: 'var(--font-heading)', fontSize: size, letterSpacing: '-0.01em' }}
    >
      jobhunt
    </span>
  );
}

const links = [
  { href: '/dashboard', label: 'Pipeline' },
  { href: '/insights', label: 'Tracker' },
  { href: '/interview', label: 'Interview prep' },
  { href: '/onboarding', label: 'Profile & résumé' },
  { href: '/tools/ats-score', label: 'ATS score' },
];

export default function Rail() {
  const path = usePathname();

  return (
    <aside
      className="fixed inset-y-0 left-0 z-20 hidden w-[220px] flex-col gap-6 p-[26px_18px] lg:flex"
      style={{ background: 'var(--color-surface)' }}
    >
      <Link href="/" className="flex items-center gap-2.5 no-underline" style={{ color: 'inherit' }}>
        <Mark />
        <Wordmark />
      </Link>

      <nav className="flex flex-col gap-1">
        {links.map((l) => {
          const active = path === l.href || path?.startsWith(`${l.href}/`);
          return (
            <Link
              key={l.href}
              href={l.href}
              className="rail-link"
              aria-current={active ? 'page' : undefined}
            >
              <span className="rail-dot" />
              {l.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
