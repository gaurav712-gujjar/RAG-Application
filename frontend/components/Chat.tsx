"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, Doc, HistoryTurn, Latency, Mode, Source, listDocs, streamChat } from "@/lib/api";

type Message = {
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

const sec = (ms?: number) => (ms === undefined || ms === null ? "–" : `${(ms / 1000).toFixed(2)} s`);
const STAGE_TEXT = {
  searching: "Searching your documents",
  web: "Searching the web",
  writing: "Writing the answer",
} as const;

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "docs", label: "Documents", hint: "Answers only from your files" },
  { id: "docs_web", label: "Documents + Web", hint: "Your files first, the web if they don’t cover it" },
  { id: "web", label: "Web", hint: "Search the web only" },
];
const MODE_KEY = "docchat_mode";

const hostOf = (url?: string) => {
  try {
    return new URL(url ?? "").hostname.replace(/^www\./, "");
  } catch {
    return url ?? "";
  }
};

/** Where the time went, as one bar: each part is as wide as the time it took. */
function Waterfall({ l }: { l: Latency }) {
  const ttft = l.serverTtftMs ?? 0;
  const retrieval = l.retrievalMs ?? 0;
  const embed = l.embedMs ?? 0;
  const search = l.searchMs ?? 0;
  const rerank = l.rerankMs ?? 0;
  const other = Math.max(0, retrieval - embed - search - rerank);
  const prep = l.prepMs ?? 0;
  const web = l.webMs ?? 0;
  const model = Math.max(0, ttft - retrieval - prep - web);
  const stream = Math.max(0, (l.totalMs ?? ttft) - ttft);

  const parts = l.cached
    ? [{ name: "Cache", ms: ttft, cls: "bg-done" }]
    : [
        { name: "Checks", ms: prep, cls: "bg-muted" },
        { name: "Embed question", ms: embed + other, cls: "bg-done" },
        { name: "Search", ms: search, cls: "bg-done/70" },
        { name: "Rerank", ms: rerank, cls: "bg-done/45" },
        { name: "Web search", ms: web, cls: "bg-done/60" },
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
  if (all.some((s) => s.kind === "web") && !m.content.startsWith("No web results")) return all.slice(0, 3);
  // No citation in the text: show only the best match, not every retrieved file.
  return m.content.startsWith("Not found") || m.content.startsWith("No web results") ? [] : all.slice(0, 1);
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
  const [mode, setMode] = useState<Mode>("docs_web");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set()); // empty = search all documents
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Remember the chosen mode between visits (storage can be blocked, so never rely on it).
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(MODE_KEY);
      if (saved === "docs" || saved === "docs_web" || saved === "web") setMode(saved);
    } catch {
      /* ignore */
    }
  }, []);
  const chooseMode = (m: Mode) => {
    setMode(m);
    try {
      window.localStorage.setItem(MODE_KEY, m);
    } catch {
      /* ignore */
    }
  };

  const loadDocs = async () => {
    try {
      setDocs((await listDocs()).filter((d) => d.status === "ready"));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
    }
  };
  useEffect(() => {
    loadDocs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const toggleDoc = (id: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const patchLast = (fn: (m: Message) => Message) =>
    setMessages((all) => all.map((m, i) => (i === all.length - 1 ? fn(m) : m)));

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    ask(question, mode, messages);
  }

  /** Re-ask the last question on the web, replacing the "Not found" answer. */
  function searchWeb(question: string) {
    if (busy) return;
    const before = messages.slice(0, -2);
    setMessages(before);
    ask(question, "web", before);
  }

  async function ask(question: string, useMode: Mode, past: Message[]) {
    const ids = [...selected].filter((id) => docs.some((d) => d.id === id));
    const history: HistoryTurn[] = past
      .filter((m) => !m.error && m.content)
      .slice(-4)
      .map((m) => ({ role: m.role, content: m.content }));

    const t0 = performance.now(); // client-side clock starts at send
    setBusy(true);
    setMessages((all) => [
      ...all,
      { role: "user", content: question },
      {
        role: "assistant", content: "", streaming: true, latency: {}, question,
        stage: useMode === "web" ? "web" : "searching", startedAt: t0,
      },
    ]);

    abortRef.current = new AbortController();
    try {
      for await (const { event, data } of streamChat(question, history, abortRef.current.signal, { mode: useMode, docIds: ids })) {
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
              webMs: data.web_ms,
              considered: data.considered,
            },
            answerSource: data.answer_source,
            webAvailable: !!data.web_available,
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
      loadDocs(); // pick up files uploaded since the last answer
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
                Answers cite the page they came from. Pick <b>Documents + Web</b> below to fall back to a web search
                when your files don’t cover the question, or <b>Web</b> to skip your files.
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
                {m.answerSource === "web" && m.content && (
                  <p className="mb-1 text-xs font-medium text-done">From the web, not your documents</p>
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
                        <span className="font-semibold text-done">[{s.n}]</span>{" "}
                        {s.kind === "web" ? (
                          <a href={s.url} target="_blank" rel="noopener noreferrer" className="underline-offset-2 hover:underline" title={s.filename}>
                            {hostOf(s.url)}
                          </a>
                        ) : (
                          <>
                            {s.filename}, page {s.page}
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {m.webAvailable && !m.streaming && m.question && i === messages.length - 1 && (
                  <button
                    type="button"
                    onClick={() => searchWeb(m.question!)}
                    disabled={busy}
                    className="mt-3 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm font-medium hover:border-ink disabled:opacity-40"
                  >
                    Search the web instead
                  </button>
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

      <form onSubmit={onSubmit} className="border-t border-line bg-panel px-4 py-3 md:px-10">
        <div className="mx-auto max-w-2xl">
          <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-2">
            <div role="group" aria-label="Where to look" className="inline-flex rounded-lg border border-line bg-white p-0.5">
              {MODES.map((o) => (
                <button
                  key={o.id}
                  type="button"
                  title={o.hint}
                  aria-pressed={mode === o.id}
                  onClick={() => chooseMode(o.id)}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                    mode === o.id ? "bg-ink text-panel" : "text-muted hover:text-ink"
                  }`}
                >
                  {o.label}
                </button>
              ))}
            </div>
            {mode !== "web" && docs.length > 0 && (
              <details className="relative text-xs" onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && loadDocs()}>
                <summary className="cursor-pointer text-muted hover:text-ink">
                  {selected.size === 0 ? `All documents (${docs.length})` : `${selected.size} of ${docs.length} documents`}
                </summary>
                <div className="absolute bottom-7 left-0 z-10 max-h-60 w-72 overflow-y-auto rounded-lg border border-line bg-panel p-2 shadow-lg">
                  <button type="button" onClick={() => setSelected(new Set())} className="mb-1 text-xs text-muted underline">
                    Use all documents
                  </button>
                  {docs.map((d) => (
                    <label key={d.id} className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 hover:bg-white">
                      <input type="checkbox" checked={selected.has(d.id)} onChange={() => toggleDoc(d.id)} />
                      <span className="truncate" title={d.filename}>{d.filename}</span>
                    </label>
                  ))}
                </div>
              </details>
            )}
          </div>
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              maxLength={2000}
              aria-label="Your question"
              placeholder={mode === "web" ? "Ask anything, answered from the web" : "Ask about your documents"}
              className="min-w-0 flex-1 rounded-lg border border-line bg-white px-3.5 py-2.5 text-sm placeholder:text-muted"
            />
            <button
              disabled={busy || !input.trim()}
              className="rounded-lg bg-ink px-5 py-2.5 text-sm font-medium text-panel transition-opacity disabled:opacity-40"
            >
              {busy ? "Answering" : "Ask"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
