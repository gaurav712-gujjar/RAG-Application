"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, HistoryTurn, Latency, Source, streamChat } from "@/lib/api";

type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  latency?: Latency;
  stage?: "searching" | "writing";
  startedAt?: number;
  error?: string;
  streaming?: boolean;
};

const sec = (ms?: number) => (ms === undefined || ms === null ? "–" : `${(ms / 1000).toFixed(2)} s`);
const STAGE_TEXT = { searching: "Searching your documents", writing: "Writing the answer" } as const;

/** Where the time went, as one bar: each part is as wide as the time it took. */
function Waterfall({ l }: { l: Latency }) {
  const ttft = l.serverTtftMs ?? 0;
  const retrieval = l.retrievalMs ?? 0;
  const embed = l.embedMs ?? 0;
  const search = l.searchMs ?? 0;
  const rerank = l.rerankMs ?? 0;
  const other = Math.max(0, retrieval - embed - search - rerank);
  const prep = l.prepMs ?? 0;
  const model = Math.max(0, ttft - retrieval - prep);
  const stream = Math.max(0, (l.totalMs ?? ttft) - ttft);

  const parts = l.cached
    ? [{ name: "Cache", ms: ttft, cls: "bg-done" }]
    : [
        { name: "Checks", ms: prep, cls: "bg-muted" },
        { name: "Embed question", ms: embed + other, cls: "bg-done" },
        { name: "Search", ms: search, cls: "bg-done/70" },
        { name: "Rerank", ms: rerank, cls: "bg-done/45" },
        { name: "Model thinks", ms: model, cls: "bg-live" },
        { name: "Streaming", ms: stream, cls: "bg-line" },
      ].filter((p) => p.ms > 0);
  const sum = parts.reduce((a, p) => a + p.ms, 0) || 1;

  return (
    <div className="mt-4 max-w-md" aria-label="Where the time went">
      <p className="text-sm">
        <span className="font-semibold">First word in {sec(ttft)}</span>
        <span className="text-muted"> · total {sec(l.totalMs)}</span>
        {l.cached && <span className="ml-2 rounded bg-done/10 px-1.5 py-0.5 text-xs text-done">from cache, no tokens used</span>}
        {l.clientTtftMs !== undefined && !l.cached && (
          <span className="text-muted" title="Measured in your browser, includes the network"> · you saw it at {sec(l.clientTtftMs)}</span>
        )}
      </p>
      <div className="mt-2 flex h-2 gap-px overflow-hidden rounded-full">
        {parts.map((p) => (
          <span key={p.name} className={p.cls} style={{ width: `${(p.ms / sum) * 100}%` }} title={`${p.name}: ${Math.round(p.ms)} ms`} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {parts.map((p) => (
          <li key={p.name} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${p.cls}`} aria-hidden />
            {p.name} {Math.round(p.ms)} ms
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Live status while we wait for the first word, so a slow start never looks like a freeze. */
function Waiting({ stage, startedAt }: { stage: keyof typeof STAGE_TEXT; startedAt: number }) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const t = setInterval(() => setNow(performance.now()), 100);
    return () => clearInterval(t);
  }, []);
  return (
    <p className="flex items-center gap-2 text-sm text-muted" role="status">
      <span className="seg-live inline-block h-2 w-6 rounded-full" aria-hidden />
      {STAGE_TEXT[stage]}… {((now - startedAt) / 1000).toFixed(1)} s
    </p>
  );
}

/** Show chips only for sources the answer actually cites; fall back to all if none are cited. */
function citedSources(m: Message): Source[] {
  const all = m.sources ?? [];
  // Models sometimes write 【1】 (full-width brackets) in Hindi answers, so accept both styles.
  const cited = new Set([...m.content.matchAll(/[\[【](\d+)[\]】]/g)].map((x) => Number(x[1])));
  const picked = all.filter((s) => cited.has(s.n));
  if (picked.length) return picked;
  // No citation in the text: show only the best match, not every retrieved file.
  return m.content.startsWith("Not found") ? [] : all.slice(0, 1);
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

    const t0 = performance.now(); // client-side clock starts at send
    setInput("");
    setBusy(true);
    setMessages((all) => [
      ...all,
      { role: "user", content: question },
      { role: "assistant", content: "", streaming: true, latency: {}, stage: "searching", startedAt: t0 },
    ]);

    abortRef.current = new AbortController();
    try {
      for await (const { event, data } of streamChat(question, history, abortRef.current.signal)) {
        if (event === "stage") {
          patchLast((m) => ({ ...m, stage: data.name }));
        } else if (event === "sources") {
          patchLast((m) => ({ ...m, sources: data }));
        } else if (event === "ttft") {
          patchLast((m) => ({
            ...m,
            latency: { ...m.latency, serverTtftMs: data.ttft_ms, retrievalMs: data.retrieval_ms, cached: data.cached },
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
            latency: {
              ...m.latency,
              totalMs: data.total_ms,
              serverTtftMs: data.ttft_ms ?? m.latency?.serverTtftMs,
              retrievalMs: data.retrieval_ms ?? m.latency?.retrievalMs,
              prepMs: data.prep_ms,
              embedMs: data.embed_ms,
              searchMs: data.search_ms,
              rerankMs: data.rerank_ms,
              considered: data.considered,
            },
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
      <div className="flex-1 overflow-y-auto px-4 py-6 md:px-10">
        <div className="mx-auto max-w-2xl space-y-8">
          {messages.length === 0 && (
            <div className="pt-16">
              <h2 className="text-2xl font-semibold tracking-tight">Ask your documents anything.</h2>
              <p className="mt-2 max-w-md text-muted">
                Answers come only from your files and cite the page they came from. If the files don’t cover it, you’ll be told.
              </p>
            </div>
          )}
          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-ink px-4 py-2.5 text-sm text-panel">
                  {m.content}
                </p>
              </div>
            ) : (
              <div key={i} className="border-l-2 border-done pl-4">
                {!m.content && m.streaming && !m.error && m.stage && m.startedAt !== undefined && (
                  <Waiting stage={m.stage} startedAt={m.startedAt} />
                )}
                {m.content && (
                  <p className="whitespace-pre-wrap text-[15px] leading-7">
                    {m.content}
                    {m.streaming && <span className="caret ml-0.5 inline-block h-4 w-0.5 translate-y-0.5 bg-ink" aria-hidden />}
                  </p>
                )}
                {m.error && (
                  <p role="alert" className="text-sm text-bad">
                    {m.error}
                  </p>
                )}
                {citedSources(m).length > 0 && (
                  <ul className="mt-3 flex flex-wrap gap-1.5">
                    {citedSources(m).map((s) => (
                      <li key={s.n} className="rounded-md border border-line bg-panel px-2 py-1 text-xs">
                        <span className="font-semibold text-done">[{s.n}]</span> {s.filename}, page {s.page}
                      </li>
                    ))}
                  </ul>
                )}
                {m.content.startsWith("Not found") && !m.streaming && (m.latency?.considered?.length ?? 0) > 0 && (
                  <details className="mt-3 text-xs text-muted">
                    <summary className="cursor-pointer">Closest passages the search looked at</summary>
                    <ul className="mt-1.5 space-y-0.5">
                      {m.latency!.considered!.map((c, k) => (
                        <li key={k}>
                          {c.filename}, page {c.page}: score {c.score.toFixed(2)} {c.kept ? "(sent to the model)" : "(too low)"}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {m.latency && m.latency.serverTtftMs !== undefined && !m.streaming && <Waterfall l={m.latency} />}
              </div>
            ),
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <form onSubmit={send} className="border-t border-line bg-panel px-4 py-3 md:px-10">
        <div className="mx-auto flex max-w-2xl gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={2000}
            aria-label="Your question"
            placeholder="Ask about your documents"
            className="min-w-0 flex-1 rounded-lg border border-line bg-white px-3.5 py-2.5 text-sm placeholder:text-muted"
          />
          <button
            disabled={busy || !input.trim()}
            className="rounded-lg bg-ink px-5 py-2.5 text-sm font-medium text-panel transition-opacity disabled:opacity-40"
          >
            {busy ? "Answering" : "Ask"}
          </button>
        </div>
      </form>
    </div>
  );
}