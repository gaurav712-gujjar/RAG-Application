"use client";

import { FormEvent, useState } from "react";
import { AdminUsage, adminUsage } from "@/lib/api";

/** Owner-only numbers. Not linked from the app; needs the ADMIN_TOKEN set on the server. */
export default function AdminPage() {
  const [token, setToken] = useState("");
  const [data, setData] = useState<AdminUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setData(await adminUsage(token.trim()));
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "Could not load");
    } finally {
      setBusy(false);
    }
  }

  const Stat = ({ label, value }: { label: string; value: string }) => (
    <div className="rounded-2xl border border-line bg-panel px-5 py-4">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
    </div>
  );

  return (
    <div className="backdrop min-h-screen p-4 md:p-8">
      <div className="mx-auto max-w-3xl rounded-3xl bg-panel p-6 shadow-xl md:p-8">
        <h1 className="text-xl font-semibold tracking-tight">Usage</h1>
        <form onSubmit={load} className="mt-4 flex gap-2">
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            aria-label="Admin token"
            placeholder="Admin token"
            className="min-w-0 flex-1 rounded-xl border border-line bg-panel px-3.5 py-2.5 text-sm focus:border-ink/50 focus:outline-none"
          />
          <button disabled={busy || !token.trim()} className="rounded-xl bg-ink px-5 py-2.5 text-sm font-medium text-panel disabled:opacity-40">
            {busy ? "Loading…" : "Show"}
          </button>
        </form>
        {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
        {data && (
          <div className="mt-6 space-y-6">
            <div className="grid gap-3 sm:grid-cols-3">
              <Stat label="Users" value={data.users.toLocaleString()} />
              <Stat label="Documents" value={data.documents.toLocaleString()} />
              <Stat label={`Tokens today (UTC ${data.date})`} value={data.tokens_today.toLocaleString()} />
            </div>
            {data.per_user.length === 0 ? (
              <p className="text-sm text-muted">No token usage recorded today. Token counts need Redis (REDIS_URL).</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-line text-xs text-muted">
                      <th className="py-2 font-medium">User</th>
                      <th className="py-2 text-right font-medium">Tokens today</th>
                      <th className="w-40 py-2 pl-4 font-medium">Of daily limit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.per_user.map((u) => {
                      const pct = data.daily_limit > 0 ? Math.min(100, (u.tokens / data.daily_limit) * 100) : 0;
                      return (
                        <tr key={u.email} className="border-b border-line/60">
                          <td className="py-2.5 pr-3">{u.email}</td>
                          <td className="py-2.5 text-right tabular-nums">{u.tokens.toLocaleString()}</td>
                          <td className="py-2.5 pl-4">
                            <span className="block h-1.5 w-full overflow-hidden rounded-full bg-line">
                              <span className={`block h-full ${pct >= 90 ? "bg-bad" : "bg-done"}`} style={{ width: `${pct}%` }} />
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
