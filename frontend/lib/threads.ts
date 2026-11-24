"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Latency, Source } from "./api";

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
const MAX_MESSAGES = 40;

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

/** Chat history kept in this browser (not on the server). */
export function useThreads() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState("");
  const [ready, setReady] = useState(false);
  const activeRef = useRef("");
  activeRef.current = activeId;

  useEffect(() => {
    const saved = read().map((t) => ({ ...t, messages: t.messages.map((m) => ({ ...m, streaming: false })) }));
    const fresh = blank();
    setThreads([fresh, ...saved]);
    setActiveId(fresh.id);
    setReady(true);
  }, []);

  // Save shortly after the last change, so a streaming answer does not rewrite storage per token.
  useEffect(() => {
    if (!ready) return;
    const t = setTimeout(() => write(threads), 400);
    return () => clearTimeout(t);
  }, [threads, ready]);

  const update = useCallback((id: string, fn: (m: Message[]) => Message[]) => {
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
