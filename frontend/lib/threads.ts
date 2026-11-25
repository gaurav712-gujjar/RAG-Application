"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Latency, Source, deleteThreadOnServer, listThreads, saveThread } from "./api";

export type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  latency?: Latency;
  stage?: "searching" | "web" | "writing";
  startedAt?: number;
  question?: string;
  answerSource?: "docs" | "web";
  webAvailable?: boolean;
  error?: string;
  streaming?: boolean;
};

export type Thread = { id: string; title: string; updatedAt: number; messages: Message[] };

const KEY = "docchat_threads_v1";
const MAX_THREADS = 30;
const MAX_MESSAGES = 60;

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
const blank = (): Thread => ({ id: uid(), title: "New chat", updatedAt: Date.now(), messages: [] });

// Only what is needed to show the conversation again; live-only fields are dropped.
function slim(m: Message): Message {
  const { stage: _s, startedAt: _t, streaming: _g, ...rest } = m;
  return { ...rest, latency: m.latency ? { ...m.latency, considered: undefined } : undefined };
}

function read(): Thread[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed = raw ? (JSON.parse(raw) as Thread[]) : [];
    return Array.isArray(parsed) ? parsed.filter((t) => t && t.id && Array.isArray(t.messages)) : [];
  } catch {
    return [];
  }
}

function write(threads: Thread[]) {
  try {
    const keep = threads
      .filter((t) => t.messages.length > 0)
      .slice(0, MAX_THREADS)
      .map((t) => ({ ...t, messages: t.messages.slice(-MAX_MESSAGES).map(slim) }));
    window.localStorage.setItem(KEY, JSON.stringify(keep));
  } catch {
    /* storage blocked or full: chats just won't survive a reload */
  }
}

export function clearThreads() {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

const titleOf = (messages: Message[]) => {
  const first = messages.find((m) => m.role === "user")?.content.trim().replace(/\s+/g, " ");
  return first ? (first.length > 48 ? `${first.slice(0, 48)}…` : first) : "New chat";
};

const toMs = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? Date.now() : t;
};
const quiet = () => undefined; // a failed background sync is retried on the next change, never shown

/** Chat history. The server is the source of truth (so chats follow you across devices); this
 *  browser keeps a copy so the list shows instantly and still works if the server is slow. */
export function useThreads() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState("");
  const [ready, setReady] = useState(false);
  const activeRef = useRef("");
  activeRef.current = activeId;
  const dirty = useRef<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    const local = read().map((t) => ({ ...t, messages: t.messages.map((m) => ({ ...m, streaming: false })) }));
    const fresh = blank();
    setThreads([fresh, ...local]);
    setActiveId(fresh.id);
    setReady(true);

    listThreads()
      .then((remote) => {
        if (!alive) return;
        const byId = new Map<string, Thread>();
        for (const t of local) {
          byId.set(t.id, t);
          dirty.current.add(t.id); // chats only this browser has are uploaded below
        }
        for (const r of remote) {
          byId.set(r.id, { id: r.id, title: r.title, updatedAt: toMs(r.updated_at), messages: r.messages as Message[] });
          dirty.current.delete(r.id);
        }
        setThreads((cur) => {
          const open = cur.filter((t) => t.id === fresh.id);
          const mine = new Map(cur.map((t) => [t.id, t]));
          const merged = [...byId.values()].map((t) => mine.get(t.id) ?? t); // keep edits made while loading
          return [...open, ...merged.filter((t) => t.id !== fresh.id)];
        });
      })
      .catch(quiet);
    return () => {
      alive = false;
    };
  }, []);

  // Save shortly after the last change, so a streaming answer does not write per token.
  useEffect(() => {
    if (!ready) return;
    const timer = setTimeout(() => {
      write(threads);
      for (const t of threads) {
        if (!dirty.current.has(t.id) || t.messages.length === 0) continue;
        if (t.messages.some((m) => m.streaming)) continue; // wait for the answer to finish
        dirty.current.delete(t.id);
        saveThread(t.id, t.title, t.messages.slice(-MAX_MESSAGES).map(slim)).catch(() => dirty.current.add(t.id));
      }
    }, 800);
    return () => clearTimeout(timer);
  }, [threads, ready]);

  const update = useCallback((id: string, fn: (m: Message[]) => Message[]) => {
    dirty.current.add(id);
    setThreads((all) =>
      all.map((t) => {
        if (t.id !== id) return t;
        const messages = fn(t.messages);
        return { ...t, messages, updatedAt: Date.now(), title: t.title === "New chat" ? titleOf(messages) : t.title };
      }),
    );
  }, []);

  const create = useCallback(() => {
    setThreads((all) => {
      const current = all.find((t) => t.id === activeRef.current);
      if (current && current.messages.length === 0) return all; // already on an empty chat
      const t = blank();
      setActiveId(t.id);
      return [t, ...all];
    });
  }, []);

  const remove = useCallback((id: string) => {
    dirty.current.delete(id);
    deleteThreadOnServer(id).catch(quiet);
    setThreads((all) => {
      const rest = all.filter((t) => t.id !== id);
      if (id !== activeRef.current) return rest;
      const next = rest[0] ?? blank();
      setActiveId(next.id);
      return rest.length ? rest : [next];
    });
  }, []);

  return { threads, activeId, ready, setActiveId, update, create, remove };
}
