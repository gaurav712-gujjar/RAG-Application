"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { authenticate, tryDemo } from "@/lib/api";
import ThemeToggle from "./ThemeToggle";
import { ChatIcon, FileIcon } from "./icons";

const COPY = {
  login: {
    title: "Welcome back",
    lead: "Sign in to keep chatting with your documents.",
    button: "Sign in",
    switchText: "New to DocChat?",
    switchLink: "Create an account",
    switchHref: "/register",
  },
  register: {
    title: "Create your account",
    lead: "Upload documents and ask questions in English or Hindi.",
    button: "Create account",
    switchText: "Already have an account?",
    switchLink: "Sign in",
    switchHref: "/login",
  },
} as const;

const PROMPTS = [
  "Ask DocChat anything about your documents.",
  "What is the refund window in this policy?",
  "इस दस्तावेज़ में वारंटी कितने महीने की है?",
  "Summarise this contract in five bullet points.",
  "Order ORD-48213 kab tak deliver hoga?",
];

/** Types each prompt, holds it, erases it, then moves to the next. Shows the first one still if motion is reduced. */
function useTyping(phrases: string[]) {
  const [text, setText] = useState(phrases[0]);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let i = 0;
    let n = 0;
    let erasing = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      const phrase = Array.from(phrases[i]); // Hindi letters are multi-code-unit: step by character
      if (!erasing) {
        n += 1;
        setText(phrase.slice(0, n).join(""));
        if (n >= phrase.length) {
          erasing = true;
          timer = setTimeout(tick, 1800);
          return;
        }
        timer = setTimeout(tick, 45);
      } else {
        n -= 1;
        setText(phrase.slice(0, n).join(""));
        if (n <= 0) {
          erasing = false;
          i = (i + 1) % phrases.length;
          timer = setTimeout(tick, 350);
          return;
        }
        timer = setTimeout(tick, 22);
      }
    };
    setText("");
    timer = setTimeout(tick, 400);
    return () => clearTimeout(timer);
  }, [phrases]);
  return text;
}

/** The colour panel on the right: a prompt box that types example questions. */
function PromptPanel() {
  const text = useTyping(PROMPTS);
  return (
    <div className="auth-art relative hidden overflow-hidden rounded-[28px] md:grid md:place-items-center" aria-hidden>
      <div className="flex w-[min(86%,520px)] items-center gap-3 rounded-2xl bg-white/90 py-3.5 pl-5 pr-3.5 shadow-lg backdrop-blur">
        <p className="min-h-[1.5rem] flex-1 text-[15px] leading-6 text-ink">
          {text}
          <span className="caret ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[3px] bg-done" />
        </p>
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-ink text-panel">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        </span>
      </div>
    </div>
  );
}

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const c = COPY[mode];
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authenticate(mode, email, password);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  async function demo() {
    setBusy(true);
    setError(null);
    try {
      await tryDemo();
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the demo");
      setBusy(false);
    }
  }

  const field = "w-full rounded-lg border border-line bg-white dark:bg-panel px-3.5 py-3 text-sm placeholder:text-muted focus:border-ink focus:outline-none";

  return (
    <div className="auth-bg grid min-h-screen md:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] md:gap-6 md:p-6">
      <main className="relative flex flex-col justify-center px-6 py-10 sm:px-12 md:px-16">
        <ThemeToggle className="absolute right-4 top-4" />
        <div className="mx-auto w-full max-w-sm">
          <p className="mb-14 flex items-center gap-2 text-lg font-semibold tracking-tight">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-panel">
              <ChatIcon width={16} height={16} />
            </span>
            DocChat
          </p>
          <h1 className="text-[2rem] font-bold leading-tight tracking-tight">{c.title}</h1>

          <button type="button" onClick={demo} disabled={busy} className="mt-8 flex w-full items-center justify-center gap-2.5 rounded-lg border border-line bg-[#f3f1ec] dark:bg-panel py-3 text-sm font-medium shadow-sm transition-colors hover:bg-[#ebe8e1] dark:hover:bg-sel disabled:opacity-50">
            <FileIcon width={16} height={16} />
            Try the demo without signing up
          </button>
          <p className="mt-2 text-center text-xs text-muted">Read-only, with sample documents. The first load can take a few seconds.</p>

          <div className="my-6 flex items-center gap-3 text-xs text-muted">
            <span className="h-px flex-1 bg-line" />
            OR
            <span className="h-px flex-1 bg-line" />
          </div>

          <form onSubmit={submit} className="space-y-5">
            <label className="block text-sm">
              <span className="mb-1.5 block font-semibold">Email</span>
              <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={field} placeholder="you@example.com" />
            </label>
            <label className="block text-sm">
              <span className="mb-1.5 block font-semibold">Password</span>
              <input
                type="password"
                required
                minLength={8}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={field}
                aria-describedby="pw-hint"
              />
              <span id="pw-hint" className="mt-1.5 block text-xs text-muted">At least 8 characters.</span>
            </label>
            {error && (
              <p role="alert" className="rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad">
                {error}
              </p>
            )}
            <button disabled={busy} className="w-full rounded-lg bg-[#2b2b2b] py-3 text-sm font-medium text-white dark:bg-ink dark:text-bg transition-opacity hover:opacity-90 disabled:opacity-50">
              {busy ? "Please wait…" : c.button}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-muted">
            {c.switchText}{" "}
            <Link href={c.switchHref} className="font-medium text-ink underline underline-offset-2">
              {c.switchLink}
            </Link>
          </p>
        </div>
      </main>

      <PromptPanel />
    </div>
  );
}
