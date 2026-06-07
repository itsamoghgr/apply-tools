"use client";

import { useEffect, useRef } from "react";

// A lightweight rich-text editor for a single bullet. The *value* is markdown
// (**bold**, *italic*); the *display* is real bold/italic via contentEditable —
// the user never sees or types LaTeX or asterisks. Cmd/Ctrl+B toggles bold on
// the current selection. On every edit we serialize the DOM back to markdown.
//
// We deliberately keep the DOM simple: only <b>/<strong> and <i>/<em> are
// meaningful; everything else serializes as plain text. This keeps the
// round-trip (markdown -> HTML -> markdown) stable and predictable.

const escapeHtml = (s: string) =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

// markdown -> HTML for display. Bold first (** **), then italic (* *).
function markdownToHtml(md: string): string {
  let html = escapeHtml(md);
  html = html.replace(/\*\*(.+?)\*\*/g, (_m, t) => `<strong>${t}</strong>`);
  html = html.replace(/(^|[^*])\*(?!\*)(.+?)\*(?!\*)/g, (_m, pre, t) => `${pre}<em>${t}</em>`);
  return html;
}

// DOM -> markdown. Walk child nodes; wrap bold/italic spans with the markers.
function htmlToMarkdown(root: Node): string {
  let out = "";
  root.childNodes.forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent ?? "";
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node as HTMLElement;
    const tag = el.tagName.toLowerCase();
    const inner = htmlToMarkdown(el);
    if (tag === "br") {
      out += " ";
    } else if (tag === "b" || tag === "strong") {
      out += inner ? `**${inner}**` : "";
    } else if (tag === "i" || tag === "em") {
      out += inner ? `*${inner}*` : "";
    } else {
      out += inner;
    }
  });
  // Collapse stray doubled markers from nested same-tags (e.g. ****).
  return out.replace(/\*\*\*\*/g, "").replace(/\*\*(\s*)\*\*/g, "$1");
}

export default function BoldEditor({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  // Sync external value -> DOM only when it differs from what's rendered, so we
  // don't clobber the caret while the user is typing.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const current = htmlToMarkdown(el);
    if (current !== value) {
      el.innerHTML = markdownToHtml(value);
    }
  }, [value]);

  function emit() {
    const el = ref.current;
    if (el) onChange(htmlToMarkdown(el));
  }

  function toggleBold() {
    document.execCommand("bold");
    emit();
    ref.current?.focus();
  }

  return (
    <div className="flex-1">
      <div className="flex items-center gap-1 mb-1">
        <button
          type="button"
          onMouseDown={(e) => {
            // preventDefault keeps the text selection while clicking the button.
            e.preventDefault();
            toggleBold();
          }}
          title="Bold (⌘/Ctrl+B)"
          className="btn btn-ghost btn-xs btn-square font-bold"
        >
          B
        </button>
        <span className="text-[10px] opacity-40">select text, then Bold (⌘/Ctrl+B)</span>
      </div>
      <div
        ref={ref}
        contentEditable
        suppressContentEditableWarning
        role="textbox"
        aria-multiline="true"
        data-placeholder={placeholder}
        onInput={emit}
        onBlur={emit}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
            e.preventDefault();
            toggleBold();
          }
        }}
        className="rb-bullet textarea textarea-bordered textarea-sm w-full min-h-[3.5rem] resize-y whitespace-pre-wrap break-words leading-relaxed border border-base-300 bg-base-100 focus:outline-none focus:border-primary"
      />
    </div>
  );
}
