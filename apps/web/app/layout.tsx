import type { Metadata } from 'next';
import { Sora, Hanken_Grotesk, IBM_Plex_Mono } from 'next/font/google';
import './globals.css';

const display = Sora({ subsets: ['latin'], weight: ['400', '500', '600'], variable: '--font-display' });
const body = Hanken_Grotesk({ subsets: ['latin'], weight: ['400', '500', '600'], variable: '--font-body' });
const mono = IBM_Plex_Mono({ subsets: ['latin'], weight: ['400', '500', '600'], variable: '--font-mono' });

export const metadata: Metadata = {
  title: 'Least-cost energy digital twin · jouleWise',
  description:
    'Industrial least-cost electricity planning and dispatch, with a factory digital twin. '
    + 'A planning and simulation model: playback is simulated, not live plant telemetry.',
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
