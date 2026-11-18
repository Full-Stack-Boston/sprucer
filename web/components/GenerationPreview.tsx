"use client";

import { useState } from "react";
import { MarkdownPreview } from "@/components/MarkdownPreview";
import { parseGenerationMarkdown, requestedArtifactLabels, artifactMatchKey } from "@/lib/parseGeneration";

type Props = {
  content: string;
  /** Types requested for this generation (e.g. cover, email) — shows gaps if the model omitted one. */
  requestedTypes?: string[];
  generationId?: string;
  createdAt?: string;
  durationMs?: number | null;
};

async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to legacy path (needed on http://Tailscale-IP)
  }

  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.width = "1px";
    ta.style.height = "1px";
    ta.style.padding = "0";
    ta.style.border = "none";
    ta.style.outline = "none";
    ta.style.boxShadow = "none";
    ta.style.background = "transparent";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function CopyableBlock({ title, body }: { title: string; body: string }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    const ok = await copyText(body);
    setStatus(ok ? "copied" : "failed");
    window.setTimeout(() => setStatus("idle"), 2200);
  }

  return (
    <article className="gen-bubble">
      <header className="gen-bubble-head">
        <h3>{title}</h3>
        <button
          className={`btn secondary gen-copy${status === "failed" ? " gen-copy-failed" : ""}`}
          type="button"
          onClick={copy}
        >
          {status === "copied" ? "Copied" : status === "failed" ? "Copy failed" : "Copy"}
        </button>
      </header>
      <div className="gen-bubble-body">
        <MarkdownPreview content={body} className="md-preview-inline" />
      </div>
      {status === "failed" ? (
        <p className="gen-copy-hint">
          Clipboard blocked in this browser context. Select the text above and copy manually
          (Ctrl/Cmd+C).
        </p>
      ) : null}
    </article>
  );
}

function MissingBlock({ title }: { title: string }) {
  return (
    <article className="gen-bubble gen-bubble-missing">
      <header className="gen-bubble-head">
        <h3>{title}</h3>
        <span className="badge-soft">Missing</span>
      </header>
      <div className="gen-bubble-body">
        <p className="muted" style={{ margin: 0 }}>
          Requested for this generation, but the model did not include a{" "}
          <strong>## {title}</strong> section. Generate again (or generate this type alone).
        </p>
      </div>
    </article>
  );
}

function formatDurationMs(ms?: number | null): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "";
  const totalSec = Math.round(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  if (m <= 0) return `${s}s`;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

/** Chat-style generation preview: artifacts as copyable blocks; hide agent emphasis. */
export function GenerationPreview({
  content,
  requestedTypes,
  generationId,
  createdAt,
  durationMs,
}: Props) {
  const parsed = parseGenerationMarkdown(content);
  const wanted = requestedArtifactLabels(requestedTypes, content);
  const presentKeys = new Set(
    parsed.artifacts.map((a) => artifactMatchKey(a.title)),
  );
  const missing = wanted.filter((label) => !presentKeys.has(artifactMatchKey(label)));

  const when =
    createdAt && !Number.isNaN(new Date(createdAt).getTime())
      ? new Date(createdAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
      : "";
  const took = formatDurationMs(durationMs);

  return (
    <div className="gen-preview">
      <header className="gen-preview-head">
        <div className="gen-preview-title">{parsed.title}</div>
        {parsed.subtitle ? <p className="gen-preview-sub">{parsed.subtitle}</p> : null}
        {generationId || when || took ? (
          <p className="gen-preview-meta">
            {generationId ? <span>{generationId}</span> : null}
            {generationId && when ? <span aria-hidden="true"> · </span> : null}
            {when ? <span>Generated {when}</span> : null}
            {(generationId || when) && took ? <span aria-hidden="true"> · </span> : null}
            {took ? <span>Took {took}</span> : null}
          </p>
        ) : null}
        {missing.length ? (
          <p className="gen-preview-warn">
            This draft is incomplete. Missing: {missing.join(", ")}.
          </p>
        ) : null}
      </header>

      {parsed.artifacts.length || missing.length ? (
        <div className="gen-bubbles">
          {parsed.artifacts.map((section) =>
            section.body ? (
              <CopyableBlock key={section.key + section.title} title={section.title} body={section.body} />
            ) : null,
          )}
          {missing.map((label) => (
            <MissingBlock key={`missing-${label}`} title={label} />
          ))}
        </div>
      ) : (
        <div className="empty-hint">No draft sections found in this generation.</div>
      )}

      {parsed.grounding.length ? (
        <details className="gen-grounding">
          <summary>Grounding notes (not part of the sendable draft)</summary>
          {parsed.grounding.map((g) => (
            <div key={g.key} className="gen-grounding-block">
              <strong>{g.title}</strong>
              <MarkdownPreview content={g.body} className="md-preview-inline" />
            </div>
          ))}
        </details>
      ) : null}
    </div>
  );
}
