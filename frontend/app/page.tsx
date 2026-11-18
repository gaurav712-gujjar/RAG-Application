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

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b bg-white px-4 py-3">
        <h1 className="font-semibold">DocChat</h1>
        <div className="flex items-center gap-4">
          <UsageBadge refreshKey={usageTick} />
          <button onClick={logout} className="text-sm text-slate-500 hover:text-slate-900">
            Sign out
          </button>
        </div>
      </header>
      <main className="grid flex-1 grid-cols-1 overflow-hidden md:grid-cols-[320px_1fr]">
        <aside className="overflow-y-auto border-b bg-slate-50 p-3 md:border-b-0 md:border-r">
          <UploadPanel onUnauthorized={logout} />
        </aside>
        <section className="overflow-hidden">
          <Chat onUnauthorized={logout} onAnswered={() => setUsageTick((n) => n + 1)} />
        </section>
      </main>
    </div>
  );
}
