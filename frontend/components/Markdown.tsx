"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Models sometimes put <br> in table cells; Markdown would show it as text, so it becomes "; ".
 * Renders an answer as Markdown (headings, lists, bold, tables). Links always open in a new tab. */
export default function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _n, ...p }) => <a {...p} target="_blank" rel="noopener noreferrer" />,
          table: ({ node: _n, ...p }) => (
            <div className="overflow-x-auto">
              <table {...p} />
            </div>
          ),
        }}
      >
        {text.replace(/<br\s*\/?>/gi, "; ")}
      </ReactMarkdown>
    </div>
  );
}
