"use client";

import { DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, Doc, deleteDoc, listDocs, uploadDoc } from "@/lib/api";
import DocPipeline from "./DocPipeline";

export default function UploadPanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handle = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) return onUnauthorized();
      setError(err instanceof Error ? err.message : "Something went wrong");
    },
    [onUnauthorized],
  );

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocs());
    } catch (err) {
      handle(err);
    } finally {
      setLoaded(true);
    }
  }, [handle]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll every second while any document is still moving through the pipeline.
  const busy = docs.some((d) => d.status === "uploaded" || d.status === "processing");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(refresh, 1000);
    return () => clearInterval(t);
  }, [busy, refresh]);

  async function upload(files: File[]) {
    if (!files.length) return;
    setUploading(true);
    setError(null);
    for (const file of files) {
      try {
        await uploadDoc(file);
      } catch (err) {
        handle(err);
      }
    }
    setUploading(false);
    if (inputRef.current) inputRef.current.value = "";
    refresh();
  }

  function onDrop(e: DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    setDragging(false);
    upload(Array.from(e.dataTransfer.files));
  }

  async function remove(id: string) {
    try {
      await deleteDoc(id);
      setDocs((d) => d.filter((x) => x.id !== id));
    } catch (err) {
      handle(err);
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex cursor-pointer flex-col gap-1 rounded-lg border border-dashed px-4 py-5 text-sm transition-colors focus-within:outline focus-within:outline-2 focus-within:outline-done ${
          dragging ? "border-done bg-done/10" : "border-line bg-panel hover:border-muted"
        }`}
      >
        <span className="font-medium">{uploading ? "Uploading…" : "Add documents"}</span>
        <span className="text-xs text-muted">Drop files here or click. PDF, DOCX, TXT or MD, up to 25 MB.</span>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.txt,.md"
          className="sr-only"
          disabled={uploading}
          onChange={(e) => upload(Array.from(e.target.files ?? []))}
        />
      </label>
      {error && (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      )}

      <ul className="flex-1 divide-y divide-line overflow-y-auto">
        {loaded && docs.length === 0 && (
          <li className="py-4 text-sm text-muted">
            Nothing here yet. Add a file and you can watch it move from upload to ready.
          </li>
        )}
        {docs.map((d) => (
          <li key={d.id} className="py-4 first:pt-0">
            <div className="mb-2 flex items-baseline justify-between gap-3">
              <span className="truncate text-sm font-medium" title={d.filename}>
                {d.filename}
              </span>
              <button
                onClick={() => remove(d.id)}
                className="shrink-0 text-xs text-muted underline-offset-2 hover:text-bad hover:underline"
              >
                Delete
              </button>
            </div>
            <DocPipeline doc={d} />
          </li>
        ))}
      </ul>
    </div>
  );
}