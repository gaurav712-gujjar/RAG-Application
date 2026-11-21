import type { Doc } from "./api";

export type Step = { key: string; label: string; doing: string; what: string; timing?: string };

/** The journey of a document, in order. `key` matches the backend `stage` value. */
export const STEPS: Step[] = [
  { key: "queued", label: "Uploaded", doing: "Waiting for a free worker", what: "Your file is saved and waits in line until a worker is free." },
  { key: "parsing", label: "Reading", doing: "Reading the text", timing: "parse_ms", what: "Pulls the text out of every page. Scanned pages are read with OCR, which takes longer." },
  { key: "splitting", label: "Splitting", doing: "Splitting into passages", timing: "split_ms", what: "Cuts the text into short passages so each answer can point to an exact page." },
  { key: "embedding", label: "Embedding", doing: "Making passages searchable", timing: "embed_ms", what: "Turns every passage into numbers that capture its meaning. Usually the slowest step." },
  { key: "indexing", label: "Indexing", doing: "Saving to the search index", timing: "index_ms", what: "Stores the passages so a question can find them in milliseconds." },
  { key: "done", label: "Ready", doing: "", what: "You can ask questions now. Every answer cites the page it came from." },
];

export const READY = STEPS.length - 1;

/** Index of the step the document is on (for a failed document: the step where it stopped). */
export function currentIndex(doc: Doc): number {
  if (doc.status === "ready") return READY;
  const i = STEPS.findIndex((s) => s.key === doc.stage);
  return i >= 0 ? i : 0;
}

export type StepState = "done" | "active" | "failed" | "pending";

export function stepState(doc: Doc, index: number): StepState {
  const cur = currentIndex(doc);
  if (index < cur) return "done";
  if (index > cur) return "pending";
  if (doc.status === "failed") return "failed";
  return cur === READY ? "done" : "active";
}

export const stepMs = (doc: Doc, step: Step): number | undefined =>
  step.timing ? (doc.timings?.[step.timing] as number | undefined) : undefined;

export const totalMs = (doc: Doc): number =>
  STEPS.reduce((sum, s) => sum + (stepMs(doc, s) ?? 0), 0);

export const fmtMs = (ms: number): string => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);