import "./globals.css";
import type { ReactNode } from "react";

export const metadata = { title: "DocChat", description: "Chat with your documents" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-slate-50 text-slate-900 antialiased">{children}</body>
    </html>
  );
}
