import type { Metadata, Viewport } from 'next';
import { Caprasimo, Figtree } from 'next/font/google';
import './globals.css';
import AssistantPanel from '@/components/AssistantPanel';

/* Self-hosted by next/font, so there is no render-blocking round trip to
 * fonts.googleapis.com. grove.css reads these two custom properties. */
const heading = Caprasimo({
  subsets: ['latin'],
  weight: '400',
  display: 'swap',
  variable: '--font-heading-src',
});

const body = Figtree({
  subsets: ['latin'],
  weight: ['400', '600', '700'],
  display: 'swap',
  variable: '--font-body-src',
});

export const metadata: Metadata = {
  title: 'JobHunt — autonomous job application copilot',
  description:
    'Evidence-backed résumé tailoring, continuous discovery, and transparent multi-agent reasoning.',
};

export const viewport: Viewport = {
  themeColor: '#f5ead8',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${heading.variable} ${body.variable}`}>
      <body className="font-sans antialiased aurora">
        {children}
        <AssistantPanel />
      </body>
    </html>
  );
}
