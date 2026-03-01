import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Listening V2',
  description: 'AI listening dictation platform',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
