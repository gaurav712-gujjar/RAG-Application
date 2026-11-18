"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, Doc, deleteDoc, listDocs, uploadDoc } from "@/lib/api";

const STATUS_STYLE: Record<Doc["status"], string> = {
  uploaded: "bg-slate-100 text-slate-700",
  processing: "bg-amber-100 text-amber-800",
  ready: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-700",
};

export default function UploadPanel({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [uploading, setUploading] = useState(false);
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
    }
  }, [handle]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while anything is still being processed (matters once ingestion is async in Phase 2).
  useEffect(() => {
    if (!docs.some((d) => d.status === "uploaded" || d.status === "processing")) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [docs, refresh]);

  async function onFiles(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    setError(null);
    for (const file of Array.from(files)) {
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

  async function remove(id: string) {
    try {
      await deleteDoc(id);
      setDocs((d) => d.filter((x) => x.id !== id));
    } catch (err) {
      handle(err);
    }
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <label className="flex cursor-pointer flex-col items-center rounded-lg border-2 border-dashed border-slate-300 bg-white p-4 text-center text-sm text-slate-600 hover:border-slate-400">
        {uploading ? "Uploading..." : "Click to upload PDF, DOCX, TXT or MD (max 25 MB)"}
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.txt,.md"
          className="hidden"
          disabled={uploading}
          onChange={(e) => onFiles(e.target.files)}
        />
      </label>
      {error && <p className="text-sm text-red-600">{error}</p>}

      <ul className="flex-1 space-y-2 overflow-y-auto">
        {docs.length === 0 && <li className="text-sm text-slate-400">No documents yet.</li>}
        {docs.map((d) => (
          <li key={d.id} className="rounded-md border bg-white p-2 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-medium" title={d.filename}>
                {d.filename}
              </span>
              <button onClick={() => remove(d.id)} className="text-xs text-slate-400 hover:text-red-600">
                Delete
              </button>
            </div>
            <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
              <span className={`rounded px-1.5 py-0.5 ${STATUS_STYLE[d.status]}`}>{d.status}</span>
              {d.status === "ready" && <span>{d.num_chunks} chunks</span>}
            </div>
            {d.error && <p className="mt-1 text-xs text-red-600">{d.error}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}
