import "@fontsource-variable/hanken-grotesk";
import "./globals.css";
import type { ReactNode } from "react";

export const metadata = { title: "DocChat", description: "Ask questions about your documents" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-bg font-sans text-ink antialiased">{children}</body>
    </html>
  );
}