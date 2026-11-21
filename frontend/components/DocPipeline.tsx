"use client";

import { useEffect, useState } from "react";
import type { Doc } from "@/lib/api";
import { STEPS, READY, currentIndex, fmtMs, stepMs, stepState, totalMs } from "@/lib/pipeline";

const SEGMENT: Record<string, string> = {
  done: "bg-done",
  active: "seg-live",
  failed: "bg-bad",
  pending: "bg-line",
};

/** Where a document is in its journey: a segmented track, plus an expandable list of every step
 *  with what it does, how long it took, and what comes next. */
export default function DocPipeline({ doc }: { doc: Doc }) {
  const cur = currentIndex(doc);
  const working = doc.status === "uploaded" || doc.status === "processing";
  const failed = doc.status === "failed";

  // Auto-open while the document is moving or broken; once the person toggles it, respect that.
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? (working || failed);

  // Seconds spent on the current step, counted from when this screen first saw the step.
  const [since, setSince] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => setSince(Date.now()), [doc.stage]);
  useEffect(() => {
    if (!working) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [working]);
  const secs = Math.max(0, Math.floor((now - since) / 1000));

  let summary: string;
  if (failed) summary = `Stopped while ${STEPS[cur].doing.toLowerCase()}`;
  else if (cur === READY) {
    const bits = [`Ready in ${fmtMs(totalMs(doc))}`];
    if (doc.timings?.pages) bits.push(`${doc.timings.pages} ${doc.timings.pages === 1 ? "page" : "pages"}`);
    bits.push(`${doc.num_chunks} passages`);
    summary = bits.join(", ");
  } else summary = `${STEPS[cur].doing}… ${secs}s. Next: ${STEPS[cur + 1].label.toLowerCase()}`;

  return (
    <div>
      <button
        type="button"
        onClick={() => setManual(!open)}
        aria-expanded={open}
        className="block w-full text-left"
      >
        <div className="flex gap-1" aria-hidden>
          {STEPS.map((s, i) => (
            <span key={s.key} className={`seg-fill h-1.5 flex-1 rounded-full ${SEGMENT[stepState(doc, i)]}`} />
          ))}
        </div>
        <p className={`mt-1.5 text-xs ${failed ? "text-bad" : "text-muted"}`}>{summary}</p>
      </button>

      {open && (
        <ol className="ml-2 mt-3 space-y-3 border-l border-line pl-4">
          {STEPS.map((step, i) => {
            const state = stepState(doc, i);
            const ms = stepMs(doc, step);
            const isNext = working && i === cur + 1;
            return (
              <li key={step.key} className="relative" aria-current={state === "active" ? "step" : undefined}>
                <span
                  className={`absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full ring-2 ring-panel ${
                    state === "done" ? "bg-done" : state === "active" ? "seg-live" : state === "failed" ? "bg-bad" : "bg-line"
                  }`}
                  aria-hidden
                />
                <div className="flex items-baseline justify-between gap-2">
                  <span className={`text-sm font-medium ${state === "pending" && !isNext ? "text-muted" : ""}`}>
                    {step.label}
                  </span>
                  <span className="shrink-0 text-xs text-muted">
                    {state === "done" && ms !== undefined && fmtMs(ms)}
                    {state === "active" && `${secs}s so far`}
                    {state === "failed" && <span className="text-bad">failed here</span>}
                    {isNext && "next"}
                  </span>
                </div>
                {(state === "active" || isNext || (state === "failed")) && (
                  <p className="mt-0.5 text-xs leading-relaxed text-muted">{step.what}</p>
                )}
              </li>
            );
          })}
        </ol>
      )}
      {failed && doc.error && <p className="mt-2 text-xs text-bad">{doc.error} Upload the file again to retry.</p>}
    </div>
  );
}