"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Chat from "@/components/Chat";
import DocsModal from "@/components/DocsModal";
import Sidebar from "@/components/Sidebar";
import { getMe, getToken, setToken } from "@/lib/api";
import { clearThreads, useThreads } from "@/lib/threads";

export default function Home() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [usageTick, setUsageTick] = useState(0);
  const [docsOpen, setDocsOpen] = useState(false);
  const [docsVersion, setDocsVersion] = useState(0);
  const [docCount, setDocCount] = useState(0);
  const [drawer, setDrawer] = useState(false); // phones: sidebar as a drawer
  const [sideOpen, setSideOpen] = useState(true); // desktop: sidebar shown or hidden
  const [email, setEmail] = useState<string | null>(null);
  const t = useThreads();

  // Read the token after mount so server and client markup match.
  useEffect(() => {
    if (getToken()) setAuthed(true);
    else router.replace("/login");
  }, [router]);

  useEffect(() => {
    try {
      if (window.localStorage.getItem("docchat_sidebar") === "closed") setSideOpen(false);
    } catch {
      /* storage blocked: keep the default */
    }
  }, []);

  useEffect(() => {
    if (authed) getMe().then((u) => setEmail(u.email)).catch(() => setEmail(null));
  }, [authed]);

  const toggleSide = () =>
    setSideOpen((open) => {
      try {
        window.localStorage.setItem("docchat_sidebar", open ? "closed" : "open");
      } catch {
        /* ignore */
      }
      return !open;
    });

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
      email={email}
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
      onCollapse={drawer ? undefined : toggleSide}
    />
  );

  return (
    <div className="backdrop h-screen md:p-3">
      <div className="mx-auto flex h-full max-w-[1500px] overflow-hidden bg-panel md:rounded-2xl md:shadow-xl">
        <aside
          className={`hidden shrink-0 overflow-hidden border-line transition-[width] duration-200 md:block ${sideOpen ? "w-[290px] border-r" : "invisible w-0"}`}
          aria-hidden={!sideOpen}
        >
          <div className="h-full w-[290px]">{side}</div>
        </aside>
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
            sidebarOpen={sideOpen}
            onToggleSidebar={toggleSide}
            onDocCount={setDocCount}
          />
        </main>
      </div>
      {docsOpen && <DocsModal onClose={closeDocs} onUnauthorized={logout} />}
    </div>
  );
}
