"use client";

import { useCallback, useEffect, useState } from "react";
import AuthForm from "@/components/AuthForm";
import Chat from "@/components/Chat";
import UploadPanel from "@/components/UploadPanel";
import UsageBadge from "@/components/UsageBadge";
import { getToken, setToken } from "@/lib/api";

export default function Home() {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [usageTick, setUsageTick] = useState(0);
  const [tab, setTab] = useState<"files" | "chat">("chat"); // small screens show one pane at a time

  // Read the token after mount so server and client markup match.
  useEffect(() => {
    setAuthed(!!getToken());
    setReady(true);
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setAuthed(false);
  }, []);

  if (!ready) return null;
  if (!authed) return <AuthForm onDone={() => setAuthed(true)} />;

  const tabCls = (t: typeof tab) =>
    `flex-1 border-b-2 py-2.5 text-sm font-medium ${tab === t ? "border-ink" : "border-transparent text-muted"}`;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-line bg-panel px-4 py-3 md:px-6">
        <h1 className="text-base font-semibold tracking-tight">DocChat</h1>
        <div className="flex items-center gap-5">
          <UsageBadge refreshKey={usageTick} />
          <button onClick={logout} className="text-sm text-muted underline-offset-2 hover:text-ink hover:underline">
            Sign out
          </button>
        </div>
      </header>

      <nav className="flex border-b border-line bg-panel md:hidden" aria-label="Switch view">
        <button className={tabCls("files")} onClick={() => setTab("files")}>
          Documents
        </button>
        <button className={tabCls("chat")} onClick={() => setTab("chat")}>
          Chat
        </button>
      </nav>

      <main className="grid flex-1 grid-cols-1 overflow-hidden md:grid-cols-[340px_1fr]">
        <aside className={`${tab === "files" ? "block" : "hidden"} overflow-y-auto border-line bg-panel p-4 md:block md:border-r`}>
          <UploadPanel onUnauthorized={logout} />
        </aside>
        <section className={`${tab === "chat" ? "block" : "hidden"} overflow-hidden md:block`}>
          <Chat onUnauthorized={logout} onAnswered={() => setUsageTick((n) => n + 1)} />
        </section>
      </main>
    </div>
  );
}