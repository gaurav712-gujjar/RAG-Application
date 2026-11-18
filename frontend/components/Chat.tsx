"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, HistoryTurn, Latency, Source, streamChat } from "@/lib/api";

type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  latency?: Latency;
  error?: string;
  streaming?: boolean;
};

const sec = (ms?: number) => (ms === undefined || ms === null ? "–" : `${(ms / 1000).toFixed(2)}s`);

function LatencyBadge({ l }: { l: Latency }) {
  return (
    <div className="mt-2 inline-flex flex-wrap items-center gap-x-2 rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
      <span>TTFT {sec(l.serverTtftMs)}</span>
      <span>·</span>
      <span>Retrieval {sec(l.retrievalMs)}</span>
      <span>·</span>
      <span>Total {sec(l.totalMs)}</span>
      {l.cached && (
        <>
          <span>·</span>
          <span className="font-medium text-emerald-700" title="Served from cache: no tokens used">
            cached
          </span>
        </>
      )}
      {l.clientTtftMs !== undefined && (
        <>
          <span>·</span>
          <span title="Measured in your browser, includes network">You saw first word in {sec(l.clientTtftMs)}</span>
        </>
      )}
    </div>
  );
}

/** Show chips only for sources the answer actually cites; fall back to all if none are cited. */
function citedSources(m: Message): Source[] {
  const all = m.sources ?? [];
  const cited = new Set([...m.content.matchAll(/\[(\d+)\]/g)].map((x) => Number(x[1])));
  const picked = all.filter((s) => cited.has(s.n));
  return picked.length ? picked : m.content.startsWith("Not found") ? [] : all;
}

export default function Chat({
  onUnauthorized,
  onAnswered,
}: {
  onUnauthorized: () => void;
  onAnswered?: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const patchLast = (fn: (m: Message) => Message) =>
    setMessages((all) => all.map((m, i) => (i === all.length - 1 ? fn(m) : m)));

  async function send(e: FormEvent) {
    e.preventDefault();
    const question = input.trim();
    if (!question || busy) return;

    const history: HistoryTurn[] = messages
      .filter((m) => !m.error && m.content)
      .slice(-4)
      .map((m) => ({ role: m.role, content: m.content }));

    setInput("");
    setBusy(true);
    setMessages((all) => [
      ...all,
      { role: "user", content: question },
      { role: "assistant", content: "", streaming: true, latency: {} },
    ]);

    const t0 = performance.now(); // client-side clock starts at send
    abortRef.current = new AbortController();
    try {
      for await (const { event, data } of streamChat(question, history, abortRef.current.signal)) {
        if (event === "sources") {
          patchLast((m) => ({ ...m, sources: data }));
        } else if (event === "ttft") {
          patchLast((m) => ({
            ...m,
            latency: {
              ...m.latency,
              serverTtftMs: data.ttft_ms,
              retrievalMs: data.retrieval_ms,
              cached: data.cached,
            },
          }));
        } else if (event === "token") {
          const first = performance.now() - t0;
          patchLast((m) => ({
            ...m,
            content: m.content + data.t,
            latency: { ...m.latency, clientTtftMs: m.latency?.clientTtftMs ?? first },
          }));
        } else if (event === "done") {
          patchLast((m) => ({
            ...m,
            streaming: false,
            latency: { ...m.latency, totalMs: data.total_ms, serverTtftMs: data.ttft_ms ?? m.latency?.serverTtftMs },
          }));
        } else if (event === "error") {
          patchLast((m) => ({ ...m, streaming: false, error: data.message }));
        }
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return onUnauthorized();
      if ((err as Error).name !== "AbortError") {
        patchLast((m) => ({ ...m, error: err instanceof Error ? err.message : "Request failed" }));
      }
    } finally {
      patchLast((m) => ({ ...m, streaming: false }));
      setBusy(false);
      onAnswered?.();
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && (
          <p className="mt-10 text-center text-sm text-slate-400">
            Upload a document, then ask a question about it.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div
              className={`max-w-[85%] rounded-2xl px-4 py-2 text-sm ${
                m.role === "user" ? "bg-slate-900 text-white" : "border bg-white"
              }`}
            >
              <p className="whitespace-pre-wrap">
                {m.content || (m.streaming && !m.error ? <span className="text-slate-400">Thinking…</span> : "")}
              </p>
              {m.error && <p className="mt-1 text-red-600">{m.error}</p>}
              {m.role === "assistant" && citedSources(m).length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {citedSources(m).map((s) => (
                    <span key={s.n} className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700">
                      [{s.n}] {s.filename} · p{s.page}
                    </span>
                  ))}
                </div>
              )}
              {m.role === "assistant" && m.latency && m.latency.serverTtftMs !== undefined && (
                <LatencyBadge l={m.latency} />
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={send} className="flex gap-2 border-t bg-white p-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          maxLength={2000}
          placeholder="Ask about your documents…"
          className="flex-1 rounded-md border px-3 py-2 text-sm"
        />
        <button
          disabled={busy || !input.trim()}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  );
}
