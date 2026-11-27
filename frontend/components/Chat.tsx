"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { ApiError, Doc, HistoryTurn, Latency, Mode, Source, getSuggestions, listDocs, streamChat } from "@/lib/api";
import type { Message } from "@/lib/threads";
import Markdown from "./Markdown";
import { CheckIcon, CloseIcon, CopyIcon, FileIcon, GlobeIcon, MenuIcon, MicIcon, PlusIcon, SendIcon, SidebarIcon } from "./icons";

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
const VOICE_KEY = "docchat_voice";
const STARTERS = [
  "Summarize my documents",
  "What are the key points?",
  "List important dates and numbers",
  "Explain the main idea in Hindi",
];

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
    <details className="mt-3 text-xs text-muted">
      <summary className="cursor-pointer select-none">
        First word in {sec(ttft)} · total {sec(l.totalMs)}
        {l.cached && " · from cache, no tokens used"}
        {l.clientTtftMs !== undefined && !l.cached && ` · you saw it at ${sec(l.clientTtftMs)}`}
      </summary>
      <div className="mt-2 max-w-md" aria-label="Where the time went">
        <div className="flex h-2 gap-px overflow-hidden rounded-full">
          {parts.map((p) => (
            <span key={p.name} className={p.cls} style={{ width: `${(p.ms / sum) * 100}%` }} title={`${p.name}: ${Math.round(p.ms)} ms`} />
          ))}
        </div>
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          {parts.map((p) => (
            <li key={p.name} className="flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${p.cls}`} aria-hidden />
              {p.name} {Math.round(p.ms)} ms
            </li>
          ))}
        </ul>
      </div>
    </details>
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

/** Sources the answer actually cites; falls back to the best match when it cites none. */
function citedSources(m: Message): Source[] {
  const all = m.sources ?? [];
  // Models sometimes write 【1】 (full-width brackets) in Hindi answers, so accept both styles.
  const cited = new Set([...m.content.matchAll(/[\[【](\d+)[\]】]/g)].map((x) => Number(x[1])));
  const picked = all.filter((s) => cited.has(s.n));
  if (picked.length) return picked;
  if (all.some((s) => s.kind === "web")) return []; // a web answer that cites nothing shows no links
  return m.content.startsWith("Not found") ? [] : all.slice(0, 1);
}

function SourceCard({ s, onOpen }: { s: Source; onOpen: (s: Source) => void }) {
  const web = s.kind === "web";
  const body = (
    <>
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-side text-done">
        {web ? <GlobeIcon width={15} height={15} /> : <FileIcon width={15} height={15} />}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[13px] font-medium">{web ? hostOf(s.url) : s.filename}</span>
        <span className="block truncate text-xs text-muted">{web ? s.filename : `Page ${s.page}`}</span>
      </span>
      <span className="ml-auto shrink-0 pl-2 text-xs font-semibold text-done">[{s.n}]</span>
    </>
  );
  const cls = "flex w-56 max-w-full items-center gap-2.5 rounded-xl border border-line bg-panel px-2.5 py-2";
  return web ? (
    <a href={s.url} target="_blank" rel="noopener noreferrer" className={`${cls} hover:border-ink/40`}>
      {body}
    </a>
  ) : s.text ? (
    <button type="button" onClick={() => onOpen(s)} className={`${cls} text-left hover:border-ink/40`} title="Show the passage">
      {body}
    </button>
  ) : (
    <div className={cls}>{body}</div>
  );
}

/** The exact passage an answer used, so a claim can be checked against the document. */
function PassageDialog({ s, onClose }: { s: Source; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/40 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="Source passage" className="flex max-h-[80vh] w-full max-w-xl flex-col rounded-2xl bg-panel shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{s.filename}</p>
            <p className="text-xs text-muted">Page {s.page} · source [{s.n}]</p>
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted hover:bg-side hover:text-ink">
            <CloseIcon />
          </button>
        </div>
        <p className="overflow-y-auto whitespace-pre-wrap px-5 py-4 text-sm leading-7">{s.text}</p>
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked: nothing to do */
        }
      }}
      className="flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs text-muted hover:bg-side hover:text-ink"
    >
      {done ? <CheckIcon width={14} height={14} /> : <CopyIcon width={14} height={14} />}
      {done ? "Copied" : "Copy"}
    </button>
  );
}

export default function Chat({
  threadId, title, messages, update, docsVersion,
  onUnauthorized, onAnswered, onOpenDocs, onOpenMenu, onDocCount, sidebarOpen, onToggleSidebar,
}: {
  threadId: string;
  title: string;
  messages: Message[];
  update: (id: string, fn: (m: Message[]) => Message[]) => void;
  docsVersion: number;
  onUnauthorized: () => void;
  onAnswered?: () => void;
  onOpenDocs: () => void;
  onOpenMenu: () => void; // phones: open the sidebar drawer
  sidebarOpen: boolean; // desktop: is the sidebar visible
  onToggleSidebar: () => void;
  onDocCount: (n: number) => void;
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>("docs_web");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set()); // empty = search all documents
  const [menuOpen, setMenuOpen] = useState(false);
  const [passage, setPassage] = useState<Source | null>(null);
  const [suggest, setSuggest] = useState<string[]>([]);
  const [micOk, setMicOk] = useState(false);
  const [listening, setListening] = useState(false);
  const [voiceLang, setVoiceLang] = useState<"en-IN" | "hi-IN">("en-IN");
  const recRef = useRef<any>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

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

  // Voice input uses the browser's own speech recognition (Chrome, Edge, Safari); hidden elsewhere.
  useEffect(() => {
    const w = window as any;
    setMicOk(!!(w.SpeechRecognition || w.webkitSpeechRecognition));
    try {
      const v = window.localStorage.getItem(VOICE_KEY);
      if (v === "hi-IN" || v === "en-IN") setVoiceLang(v);
    } catch {
      /* ignore */
    }
    return () => recRef.current?.abort?.();
  }, []);
  const chooseVoice = (v: "en-IN" | "hi-IN") => {
    setVoiceLang(v);
    try {
      window.localStorage.setItem(VOICE_KEY, v);
    } catch {
      /* ignore */
    }
  };
  function toggleMic() {
    const w = window as any;
    const SR = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!SR) return;
    if (listening) {
      recRef.current?.stop();
      return;
    }
    const rec = new SR();
    rec.lang = voiceLang;
    rec.interimResults = true;
    rec.continuous = false;
    const base = input.trim() ? `${input.trim()} ` : "";
    rec.onresult = (e: any) => {
      let said = "";
      for (let i = 0; i < e.results.length; i++) said += e.results[i][0].transcript;
      setInput((base + said).slice(0, 2000));
      requestAnimationFrame(growTextarea);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recRef.current = rec;
    setListening(true);
    try {
      rec.start();
    } catch {
      setListening(false);
    }
  }

  // Starter questions written from the documents (one small LLM call per document, then stored).
  useEffect(() => {
    if (messages.length > 0 || docs.length === 0) return;
    const ids = selected.size ? [...selected] : docs.slice(0, 2).map((d) => d.id);
    let alive = true;
    Promise.allSettled(ids.map((id) => getSuggestions(id))).then((res) => {
      if (!alive) return;
      const qs = res.flatMap((r) => (r.status === "fulfilled" ? r.value.questions : []));
      setSuggest([...new Set(qs)].slice(0, 4));
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docs, selected, messages.length === 0]);

  const loadDocs = async () => {
    try {
      const ready = (await listDocs()).filter((d) => d.status === "ready");
      setDocs(ready);
      onDocCount(ready.length);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
    }
  };
  useEffect(() => {
    loadDocs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docsVersion]);

  // Close the + menu on outside click or Escape.
  useEffect(() => {
    if (!menuOpen) return;
    const away = (e: MouseEvent) => !menuRef.current?.contains(e.target as Node) && setMenuOpen(false);
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [menuOpen]);

  const toggleDoc = (id: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const growTextarea = () => {
    const el = textRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  };

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const question = input.trim();
    if (!question || busy) return;
    recRef.current?.stop?.();
    setInput("");
    requestAnimationFrame(growTextarea);
    ask(question, mode, messages);
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  /** Re-ask the last question on the web, replacing the "Not found" answer. */
  function searchWeb(question: string) {
    if (busy) return;
    const before = messages.slice(0, -2);
    update(threadId, () => before);
    ask(question, "web", before);
  }

  async function ask(question: string, useMode: Mode, past: Message[]) {
    const tid = threadId; // keep writing to this chat even if the person opens another one
    const patchLast = (fn: (m: Message) => Message) =>
      update(tid, (all) => all.map((m, i) => (i === all.length - 1 ? fn(m) : m)));

    const ids = [...selected].filter((id) => docs.some((d) => d.id === id));
    const history: HistoryTurn[] = past
      .filter((m) => !m.error && m.content)
      .slice(-4)
      .map((m) => ({ role: m.role, content: m.content }));

    const t0 = performance.now(); // client-side clock starts at send
    setBusy(true);
    update(tid, (all) => [
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

  const modeLabel = MODES.find((m) => m.id === mode)!.label;
  const empty = messages.length === 0;

  return (
    <div className="flex h-full min-w-0 flex-col">
      <header className="flex items-center gap-3 border-b border-line px-4 py-3 md:px-6">
        <button onClick={onOpenMenu} aria-label="Open menu" className="rounded-md p-1.5 text-muted hover:bg-side md:hidden">
          <MenuIcon />
        </button>
        {!sidebarOpen && (
          <button onClick={onToggleSidebar} aria-label="Open sidebar" title="Open sidebar" className="hidden rounded-md p-1.5 text-muted hover:bg-side hover:text-ink md:block">
            <SidebarIcon />
          </button>
        )}
        <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-muted">{title}</h1>
        <span className="shrink-0 rounded-full bg-side px-2.5 py-1 text-xs text-muted" title={MODES.find((m) => m.id === mode)!.hint}>
          {modeLabel}
        </span>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
        <div className="mx-auto max-w-3xl space-y-8">
          {empty && (
            <div className="pt-10 md:pt-20">
              <h2 className="text-3xl font-semibold tracking-tight">What do you want to know?</h2>
              <p className="mt-2 max-w-lg text-muted">
                {docs.length === 0
                  ? "Add a document to ask about it, or switch to Web to search the internet."
                  : `Asking across ${selected.size || docs.length} document${(selected.size || docs.length) === 1 ? "" : "s"}. Every answer shows where it came from.`}
              </p>
              <div className="mt-6 flex flex-wrap gap-2">
                {docs.length === 0 && (
                  <button onClick={onOpenDocs} className="rounded-full bg-ink px-4 py-2 text-sm font-medium text-panel">
                    Add documents
                  </button>
                )}
                {(suggest.length ? suggest : STARTERS).map((s) => (
                  <button
                    key={s}
                    onClick={() => !busy && ask(s, mode, messages)}
                    disabled={busy}
                    className="rounded-full border border-line bg-panel px-4 py-2 text-sm hover:border-ink/40 disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-sel px-4 py-2.5 text-[15px]">
                  {m.content}
                </p>
              </div>
            ) : (
              <div key={i} className="min-w-0">
                {!m.content && m.streaming && !m.error && m.stage && m.startedAt !== undefined && (
                  <Waiting stage={m.stage} startedAt={m.startedAt} />
                )}
                {m.answerSource === "web" && m.content && (
                  <p className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-done/10 px-2.5 py-1 text-xs font-medium text-done">
                    <GlobeIcon width={13} height={13} /> From the web, not your documents
                  </p>
                )}
                {m.content && (
                  <div>
                    <Markdown text={m.content} />
                    {m.streaming && <span className="caret ml-0.5 inline-block h-4 w-0.5 translate-y-0.5 bg-ink" aria-hidden />}
                  </div>
                )}
                {m.error && (
                  <p role="alert" className="text-sm text-bad">
                    {m.error}
                  </p>
                )}
                {citedSources(m).length > 0 && (
                  <div className="mt-4">
                    <p className="mb-2 text-xs font-medium text-muted">Sources</p>
                    <div className="flex flex-wrap gap-2">
                      {citedSources(m).map((s) => (
                        <SourceCard key={s.n} s={s} onOpen={setPassage} />
                      ))}
                    </div>
                  </div>
                )}
                {m.webAvailable && !m.streaming && m.question && i === messages.length - 1 && (
                  <button
                    type="button"
                    onClick={() => searchWeb(m.question!)}
                    disabled={busy}
                    className="mt-4 inline-flex items-center gap-2 rounded-full border border-line bg-panel px-4 py-2 text-sm font-medium hover:border-ink/40 disabled:opacity-40"
                  >
                    <GlobeIcon width={15} height={15} /> Search the web instead
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
                {!m.streaming && m.content && (
                  <div className="mt-2 flex items-center gap-1">
                    <CopyButton text={m.content} />
                  </div>
                )}
                {m.latency && m.latency.serverTtftMs !== undefined && !m.streaming && <Waterfall l={m.latency} />}
              </div>
            ),
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <form onSubmit={submit} className="px-4 pb-4 md:px-8 md:pb-6">
        {mode !== "web" && selected.size > 0 && (
          <p className="mx-auto mb-2 max-w-3xl px-4 text-xs text-muted">
            {selected.size} document{selected.size === 1 ? "" : "s"} selected
          </p>
        )}
        <div className="relative mx-auto flex max-w-3xl items-end gap-1.5 rounded-[28px] border border-line bg-panel p-2 shadow-sm focus-within:border-ink/40">
            <div ref={menuRef} className="relative">
              <button
                type="button"
                onClick={() => setMenuOpen((o) => !o)}
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                aria-label="Tools"
                title="Tools"
                className="grid h-10 w-10 place-items-center rounded-full text-muted hover:bg-side hover:text-ink"
              >
                <PlusIcon width={20} height={20} />
              </button>
              {menuOpen && (
                <div role="menu" className="absolute bottom-14 left-0 z-20 w-80 max-w-[calc(100vw-3rem)] rounded-2xl border border-line bg-panel p-2 shadow-xl">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      onOpenDocs();
                    }}
                    className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm font-medium hover:bg-side"
                  >
                    <FileIcon className="text-done" /> Upload or manage documents
                  </button>
                  {micOk && (
                    <>
                      <p className="px-3 pb-1 pt-3 text-xs font-medium text-muted">Voice language</p>
                      <div className="flex gap-2 px-3 pb-1">
                        {([["en-IN", "English"], ["hi-IN", "हिन्दी"]] as const).map(([v, label]) => (
                          <button
                            key={v}
                            type="button"
                            onClick={() => chooseVoice(v)}
                            aria-pressed={voiceLang === v}
                            className={`rounded-full border px-3 py-1 text-xs ${voiceLang === v ? "border-ink bg-ink text-panel" : "border-line hover:border-ink/40"}`}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                  <p className="px-3 pb-1 pt-3 text-xs font-medium text-muted">Where to look</p>
                  {MODES.map((o) => (
                    <button
                      key={o.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={mode === o.id}
                      onClick={() => chooseMode(o.id)}
                      className={`flex w-full items-start gap-2.5 rounded-xl px-3 py-2 text-left text-sm hover:bg-side ${mode === o.id ? "bg-sel/60" : ""}`}
                    >
                      <span className={`mt-1 h-3.5 w-3.5 shrink-0 rounded-full border ${mode === o.id ? "border-4 border-ink" : "border-line"}`} aria-hidden />
                      <span>
                        <span className="block font-medium">{o.label}</span>
                        <span className="block text-xs text-muted">{o.hint}</span>
                      </span>
                    </button>
                  ))}
                  {mode !== "web" && docs.length > 0 && (
                    <>
                      <div className="flex items-center justify-between px-3 pb-1 pt-3">
                        <p className="text-xs font-medium text-muted">
                          {selected.size === 0 ? `All documents (${docs.length})` : `${selected.size} of ${docs.length} documents`}
                        </p>
                        {selected.size > 0 && (
                          <button type="button" onClick={() => setSelected(new Set())} className="text-xs text-muted underline">
                            Use all
                          </button>
                        )}
                      </div>
                      <div className="max-h-40 overflow-y-auto">
                        {docs.map((d) => (
                          <label key={d.id} className="flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-1.5 text-sm hover:bg-side">
                            <input type="checkbox" checked={selected.has(d.id)} onChange={() => toggleDoc(d.id)} />
                            <span className="truncate" title={d.filename}>{d.filename}</span>
                          </label>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
            <textarea
              ref={textRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                growTextarea();
              }}
              onKeyDown={onKey}
              rows={1}
              maxLength={2000}
              aria-label="Your question"
              placeholder={mode === "web" ? "Ask anything, answered from the web" : "Send a message"}
              className="block max-h-[180px] min-w-0 flex-1 resize-none self-center bg-transparent px-1 py-2 text-[15px] placeholder:text-muted focus:outline-none"
          />
            {micOk && (
              <button
                type="button"
                onClick={toggleMic}
                aria-pressed={listening}
                aria-label={listening ? "Stop voice input" : "Voice input"}
                title={voiceLang === "hi-IN" ? "Voice input (हिन्दी)" : "Voice input (English)"}
                className={`grid h-10 w-10 shrink-0 place-items-center rounded-full ${listening ? "bg-bad/15 text-bad" : "text-muted hover:bg-side hover:text-ink"}`}
              >
                <MicIcon />
              </button>
            )}
            <button
              disabled={busy || !input.trim()}
              aria-label={busy ? "Answering" : "Send"}
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-ink text-panel transition-opacity disabled:opacity-30"
            >
              {busy ? <span className="seg-live h-2 w-4 rounded-full" /> : <SendIcon />}
            </button>
        </div>
      </form>
      {passage && <PassageDialog s={passage} onClose={() => setPassage(null)} />}
    </div>
  );
}
