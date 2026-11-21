"use client";

import { useEffect, useState } from "react";
import { Usage, getUsage } from "@/lib/api";

/** Daily token usage. `refreshKey` changes after every answer so the number stays current. */
export default function UsageBadge({ refreshKey }: { refreshKey: number }) {
  const [usage, setUsage] = useState<Usage | null>(null);

  useEffect(() => {
    getUsage().then(setUsage).catch(() => setUsage(null));
  }, [refreshKey]);

  if (!usage) return null;
  const limited = usage.daily_limit > 0;
  const pct = limited ? Math.min(100, (usage.used_today / usage.daily_limit) * 100) : 0;
  return (
    <div className="flex items-center gap-2 text-xs text-muted" title="Tokens used today. Resets at 00:00 UTC.">
      <span>
        {usage.used_today.toLocaleString()}
        {limited && ` of ${usage.daily_limit.toLocaleString()}`} tokens today
      </span>
      {limited && (
        <span className="h-1.5 w-16 overflow-hidden rounded-full bg-line">
          <span className={`block h-full ${pct >= 90 ? "bg-bad" : "bg-done"}`} style={{ width: `${pct}%` }} />
        </span>
      )}
    </div>
  );
}