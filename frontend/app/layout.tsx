import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'JobHunt — autonomous job application copilot',
  description:
    'Evidence-backed résumé tailoring, continuous discovery, and transparent multi-agent reasoning.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased aurora">{children}</body>
    </html>
  );
}
