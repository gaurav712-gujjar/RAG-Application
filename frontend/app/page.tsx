"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Chat from "@/components/Chat";
import DocsModal from "@/components/DocsModal";
import Sidebar from "@/components/Sidebar";
import { getToken, setToken } from "@/lib/api";
import { clearThreads, useThreads } from "@/lib/threads";

export default function Home() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [usageTick, setUsageTick] = useState(0);
  const [docsOpen, setDocsOpen] = useState(false);
  const [docsVersion, setDocsVersion] = useState(0);
  const [docCount, setDocCount] = useState(0);
  const [drawer, setDrawer] = useState(false); // phones: sidebar as a drawer
  const t = useThreads();

  // Read the token after mount so server and client markup match.
  useEffect(() => {
    if (getToken()) setAuthed(true);
    else router.replace("/login");
  }, [router]);

  const logout = useCallback(() => {
    setToken(null);
    clearThreads();
    router.replace("/login");
  }, [router]);

  const closeDocs = () => {
    setDocsOpen(false);
    setDocsVersion((v) => v + 1); // refresh the document list in the chat
  };

  if (!authed || !t.ready) return <div className="backdrop h-screen" />;
  const active = t.threads.find((x) => x.id === t.activeId) ?? t.threads[0];

  const side = (
    <Sidebar
      threads={t.threads}
      activeId={t.activeId}
      usageTick={usageTick}
      docCount={docCount}
      onSelect={(id) => {
        t.setActiveId(id);
        setDrawer(false);
      }}
      onNew={() => {
        t.create();
        setDrawer(false);
      }}
      onDelete={t.remove}
      onOpenDocs={() => {
        setDocsOpen(true);
        setDrawer(false);
      }}
      onLogout={logout}
      onClose={drawer ? () => setDrawer(false) : undefined}
    />
  );

  return (
    <div className="backdrop h-screen md:p-3">
      <div className="mx-auto flex h-full max-w-[1500px] overflow-hidden bg-panel md:rounded-2xl md:shadow-xl">
        <aside className="hidden w-[290px] shrink-0 border-r border-line md:block">{side}</aside>
        {drawer && (
          <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Menu">
            <div className="absolute inset-0 bg-ink/40" onClick={() => setDrawer(false)} />
            <div className="absolute inset-y-0 left-0 w-[85%] max-w-[320px] shadow-2xl">{side}</div>
          </div>
        )}
        <main className="min-w-0 flex-1">
          <Chat
            key={active.id}
            threadId={active.id}
            title={active.title}
            messages={active.messages}
            update={t.update}
            docsVersion={docsVersion}
            onUnauthorized={logout}
            onAnswered={() => setUsageTick((n) => n + 1)}
            onOpenDocs={() => setDocsOpen(true)}
            onOpenMenu={() => setDrawer(true)}
            onDocCount={setDocCount}
          />
        </main>
      </div>
      {docsOpen && <DocsModal onClose={closeDocs} onUnauthorized={logout} />}
    </div>
  );
}
