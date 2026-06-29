'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const tabs = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/onboarding', label: 'Profile & résumé' },
];

export default function Nav({ right }: { right?: React.ReactNode }) {
  const path = usePathname();
  return (
    <header className="relative z-20 flex items-center justify-between px-6 py-4">
      <Link href="/" className="flex items-center gap-2 font-extrabold tracking-tight">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-grad text-bg">J</span>
        <span>Job<span className="text-grad">Hunt</span></span>
      </Link>
      <nav className="glass hidden rounded-full p-1 sm:flex">
        {tabs.map((t) => {
          const active = path?.startsWith(t.href);
          return (
            <Link
              key={t.href}
              href={t.href}
              className={`rounded-full px-4 py-1.5 text-sm transition ${
                active ? 'bg-grad text-bg font-semibold' : 'text-muted hover:text-ink'
              }`}
            >
              {t.label}
            </Link>
          );
        })}
      </nav>
      <div className="flex items-center gap-3">{right}</div>
    </header>
  );
}
