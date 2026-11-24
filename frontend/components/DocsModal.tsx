"use client";

import { useEffect, useRef } from "react";
import UploadPanel from "./UploadPanel";
import { CloseIcon } from "./icons";

/** Documents live in a dialog (opened from the sidebar or the + menu) so the chat keeps the whole screen. */
export default function DocsModal({ onClose, onUnauthorized }: { onClose: () => void; onUnauthorized: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/40 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="Documents" className="flex max-h-[85vh] w-full max-w-lg flex-col rounded-2xl bg-panel shadow-2xl">
        <div className="flex items-center justify-between border-b border-line px-5 py-4">
          <div>
            <h2 className="text-base font-semibold">Documents</h2>
            <p className="text-xs text-muted">Add files, then watch each one go from upload to ready.</p>
          </div>
          <button ref={closeRef} onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted hover:bg-side hover:text-ink">
            <CloseIcon />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <UploadPanel onUnauthorized={onUnauthorized} />
        </div>
      </div>
    </div>
  );
}
