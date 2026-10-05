'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Mark, Wordmark } from './Rail';

// The small-screen top bar. Rail.tsx is the primary shell at lg+ (it renders
// `hidden lg:flex`), so this is the mirror breakpoint: visible below lg only.
const tabs = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/insights', label: 'Insights' },
  { href: '/interview', label: 'Interview prep' },
  { href: '/onboarding', label: 'Profile & résumé' },
];

export default function Nav({ right }: { right?: React.ReactNode }) {
  const path = usePathname();
  return (
    <header
      className="relative z-20 flex items-center justify-between gap-3 px-4 py-3 lg:hidden"
      style={{ background: 'var(--color-surface)' }}
    >
      <Link href="/dashboard" className="flex items-center gap-2 no-underline" style={{ color: 'inherit' }}>
        <Mark size={24} />
        <Wordmark size={18} />
      </Link>
      <nav className="hidden flex-1 items-center justify-center gap-1 sm:flex">
        {tabs.map((t) => {
          const active = path?.startsWith(t.href);
          return (
            <Link key={t.href} href={t.href} className="rail-link" aria-current={active ? 'page' : undefined}>
              {t.label}
            </Link>
          );
        })}
      </nav>
      <div className="flex items-center gap-3">{right}</div>
    </header>
  );
}
