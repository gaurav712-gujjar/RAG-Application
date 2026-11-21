"use client";

import { FormEvent, useState } from "react";
import { authenticate } from "@/lib/api";
import { STEPS } from "@/lib/pipeline";

export default function AuthForm({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
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
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const field = "w-full rounded-lg border border-line bg-white px-3.5 py-2.5 text-sm placeholder:text-muted";

  return (
    <div className="grid min-h-screen md:grid-cols-[1.1fr_1fr]">
      <aside className="flex flex-col justify-between bg-ink px-8 py-10 text-panel md:px-14">
        <p className="text-lg font-semibold tracking-tight">DocChat</p>
        <div className="my-12 max-w-md">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight md:text-4xl">
            Your files in. Cited answers out.
          </h1>
          <p className="mt-4 text-panel/70">
            Every document goes through the same six steps before you can ask it anything. You can watch each one.
          </p>
          <ol className="mt-8 flex gap-1.5" aria-label="Document steps">
            {STEPS.map((s, i) => (
              <li key={s.key} className="flex-1">
                <span className={`block h-1.5 rounded-full ${i < 3 ? "bg-done" : i === 3 ? "seg-live" : "bg-panel/20"}`} />
                <span className="mt-2 block text-xs text-panel/70">{s.label}</span>
              </li>
            ))}
          </ol>
        </div>
        <p className="text-xs text-panel/50">English and Hindi documents. Scanned PDFs are read with OCR.</p>
      </aside>

      <main className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm space-y-4">
          <h2 className="text-xl font-semibold">{mode === "login" ? "Sign in" : "Create your account"}</h2>
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Email</span>
            <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={field} />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Password</span>
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
            <span id="pw-hint" className="mt-1 block text-xs text-muted">At least 8 characters.</span>
          </label>
          {error && (
            <p role="alert" className="text-sm text-bad">
              {error}
            </p>
          )}
          <button disabled={busy} className="w-full rounded-lg bg-ink py-2.5 text-sm font-medium text-panel disabled:opacity-50">
            {busy ? "Please wait" : mode === "login" ? "Sign in" : "Create account"}
          </button>
          <button
            type="button"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
            className="w-full text-sm text-muted underline underline-offset-2 hover:text-ink"
          >
            {mode === "login" ? "New here? Create an account" : "Already have an account? Sign in"}
          </button>
        </form>
      </main>
    </div>
  );
}