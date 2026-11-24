"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { authenticate } from "@/lib/api";
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

/** A small, static preview of an answer, so the sign-in screen shows what the app does. */
function Preview() {
  return (
    <div className="w-full max-w-sm space-y-4" aria-hidden>
      <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-panel px-4 py-2.5 text-sm shadow-sm">
        How many days do I have to ask for a refund?
      </p>
      <div className="rounded-2xl bg-panel p-4 text-sm shadow-sm">
        <p className="leading-6">
          You have <b>14 days</b> from delivery to request a refund. Items must be unused. [1]
        </p>
        <div className="mt-3 flex items-center gap-2.5 rounded-xl border border-line px-2.5 py-2">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-side text-done">
            <FileIcon width={15} height={15} />
          </span>
          <span className="text-[13px] font-medium">refund_policy.pdf</span>
          <span className="text-xs text-muted">Page 1</span>
        </div>
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

  const field = "w-full rounded-xl border border-line bg-panel px-3.5 py-3 text-sm placeholder:text-muted focus:border-ink/50 focus:outline-none";

  return (
    <div className="backdrop grid min-h-screen place-items-center p-4">
      <div className="grid w-full max-w-4xl overflow-hidden rounded-3xl bg-panel shadow-xl md:grid-cols-[1fr_1.05fr]">
        <aside className="hidden flex-col justify-between bg-side p-10 md:flex">
          <p className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-panel">
              <ChatIcon width={16} height={16} />
            </span>
            DocChat
          </p>
          <Preview />
          <p className="text-sm text-muted">Every answer shows the page it came from.</p>
        </aside>

        <main className="flex flex-col justify-center p-7 sm:p-12">
          <p className="mb-8 flex items-center gap-2 text-lg font-semibold tracking-tight md:hidden">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-panel">
              <ChatIcon width={16} height={16} />
            </span>
            DocChat
          </p>
          <h1 className="text-2xl font-semibold tracking-tight">{c.title}</h1>
          <p className="mt-1.5 text-sm text-muted">{c.lead}</p>

          <form onSubmit={submit} className="mt-7 space-y-4">
            <label className="block text-sm">
              <span className="mb-1.5 block font-medium">Email</span>
              <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={field} placeholder="you@example.com" />
            </label>
            <label className="block text-sm">
              <span className="mb-1.5 block font-medium">Password</span>
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
            <button disabled={busy} className="w-full rounded-xl bg-ink py-3 text-sm font-medium text-panel transition-opacity disabled:opacity-50">
              {busy ? "Please wait…" : c.button}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-muted">
            {c.switchText}{" "}
            <Link href={c.switchHref} className="font-medium text-ink underline underline-offset-2">
              {c.switchLink}
            </Link>
          </p>
        </main>
      </div>
    </div>
  );
}
