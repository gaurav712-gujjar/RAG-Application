"use client";

import { useMemo, useState } from "react";
import type { Thread } from "@/lib/threads";
import UsageBadge from "./UsageBadge";
import { ChatIcon, CloseIcon, LogoutIcon, PlusIcon, SearchIcon, StackIcon, TrashIcon } from "./icons";

export default function Sidebar({
  threads, activeId, usageTick, docCount,
  onSelect, onNew, onDelete, onOpenDocs, onLogout, onClose,
}: {
  threads: Thread[];
  activeId: string;
  usageTick: number;
  docCount: number;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onOpenDocs: () => void;
  onLogout: () => void;
  onClose?: () => void; // only on phones, where the sidebar is a drawer
}) {
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    const term = q.trim().toLowerCase();
    return [...threads]
      .filter((t) => t.messages.length > 0 || t.id === activeId)
      .filter((t) => !term || t.title.toLowerCase().includes(term))
      .sort((a, b) => b.updatedAt - a.updatedAt);
  }, [threads, q, activeId]);

  return (
    <div className="flex h-full flex-col gap-3 bg-side p-3">
      <div className="flex items-center justify-between px-1.5 pt-1">
        <p className="flex items-center gap-2 text-[17px] font-semibold tracking-tight">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-ink text-panel">
            <ChatIcon width={15} height={15} />
          </span>
          DocChat
        </p>
        {onClose && (
          <button onClick={onClose} aria-label="Close menu" className="rounded-md p-1.5 text-muted hover:bg-sel/60 hover:text-ink">
            <CloseIcon />
          </button>
        )}
      </div>

      <div className="flex gap-2">
        <label className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-line bg-panel px-3 py-2 text-sm focus-within:border-ink/40">
          <SearchIcon width={16} height={16} className="shrink-0 text-muted" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search chats"
            aria-label="Search chats"
            className="min-w-0 flex-1 bg-transparent placeholder:text-muted focus:outline-none"
          />
        </label>
        <button
          onClick={onNew}
          aria-label="New chat"
          title="New chat"
          className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-xl border border-line bg-panel hover:border-ink/40"
        >
          <PlusIcon />
        </button>
      </div>

      <nav aria-label="Chats" className="-mx-1 flex-1 space-y-1 overflow-y-auto px-1">
        {shown.length === 0 && <p className="px-2 py-3 text-sm text-muted">No chats match “{q}”.</p>}
        {shown.map((t) => {
          const active = t.id === activeId;
          return (
            <div
              key={t.id}
              className={`group flex items-center rounded-xl text-sm transition-colors ${
                active ? "bg-sel font-medium" : "hover:bg-panel/70"
              }`}
            >
              <button
                onClick={() => onSelect(t.id)}
                aria-current={active ? "true" : undefined}
                className="min-w-0 flex-1 truncate px-3 py-2.5 text-left"
                title={t.title}
              >
                {t.title}
              </button>
              {t.messages.length > 0 && (
                <button
                  onClick={() => onDelete(t.id)}
                  aria-label={`Delete chat ${t.title}`}
                  className="mr-1.5 rounded-md p-1.5 text-muted opacity-0 hover:text-bad focus:opacity-100 group-hover:opacity-100"
                >
                  <TrashIcon width={15} height={15} />
                </button>
              )}
            </div>
          );
        })}
      </nav>

      <div className="space-y-2 border-t border-line pt-3">
        <button
          onClick={onOpenDocs}
          className="flex w-full items-center gap-2.5 rounded-xl bg-panel px-3 py-2.5 text-left text-sm font-medium shadow-[0_0_0_1px_rgb(var(--line))] hover:shadow-[0_0_0_1px_rgb(var(--ink)/0.4)]"
        >
          <StackIcon className="text-done" />
          <span className="flex-1">Documents</span>
          <span className="rounded-full bg-side px-2 py-0.5 text-xs text-muted">{docCount}</span>
        </button>
        <div className="flex items-center justify-between gap-2 px-1">
          <UsageBadge refreshKey={usageTick} />
          <button
            onClick={onLogout}
            aria-label="Sign out"
            title="Sign out"
            className="rounded-md p-1.5 text-muted hover:bg-sel/60 hover:text-ink"
          >
            <LogoutIcon />
          </button>
        </div>
      </div>
    </div>
  );
}
