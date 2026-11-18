"use client";

import { FormEvent, useCallback, useEffect, useId, useState } from "react";
import { GenerationPreview } from "@/components/GenerationPreview";
import { RequireAuth } from "@/components/RequireAuth";
import { TrashIcon } from "@/components/TrashIcon";
import { apiFetch } from "@/lib/api";

type AppRow = {
  id: string;
  company?: string;
  title?: string;
  status?: string;
  location?: string;
};

type Generation = {
  id: string;
  approval?: string;
  types?: string[];
  createdAt?: string;
  durationMs?: number | null;
};

type AppDetail = AppRow & {
  notes?: string;
  jdText?: string;
  generations?: Generation[];
  url?: string;
};

type IngestMode = "paste" | "url" | "upload";

const ARTIFACTS = [
  { id: "cover", label: "Cover letter" },
  { id: "email", label: "Email" },
  { id: "resume", label: "Resume" },
  { id: "interview", label: "Interview prep" },
  { id: "linkedin", label: "LinkedIn DM" },
] as const;

/**
 * Status blurbs while generate runs. Shown randomly (not time-sequenced),
 * so keep them timeless — no "N minutes in" lines.
 */
const GENERATE_TIPS = [
  "Grounding the draft in your Knowledge bank facts…",
  "Matching strengths to this job description…",
  "Outlining the sections you asked for…",
  "Local models can take a while. Still working…",
  "Writing sendable copy from facts you can defend…",
  "Checking claims against your truth bank…",
  "Shaping tone so it sounds like you, not generic AI…",
  "Building each requested artifact in turn…",
  "Still generating; no freeze on our side…",
  "Weaving JD keywords into evidence you actually have…",
  "Tightening structure and headings…",
  "Keeping punctuation keyboard-only and claims honest…",
  "Multi-type runs split into smaller model batches…",
  "Polishing as tokens arrive from the local model…",
  "Finishing a batch, then starting the next if needed…",
  "Cover, email, resume, prep, and DM each take their turn…",
  "Still working through the remaining artifact types…",
  "Truth-checking facts before the next section…",
  "Drafting the next section without inventing claims…",
  "Holding for the model; the UI is not frozen…",
  "Assembling sections as each batch returns…",
  "Interview prep and long resumes are the slower batches…",
  "Keeping never-claim guardrails in mind while writing…",
  "Working the next artifact type in the queue…",
  "Merging finished batches into one draft…",
  "Waiting on the next model response in the queue…",
];

/** Always shown once generate is ready to finish, then the wait ends. */
const GENERATE_TIP_FINAL = "Finishing up. Your draft will appear here when ready…";

const TIP_ROTATE_MS = 14_000;
const TIP_FINAL_HOLD_MS = 1_400;

function pickRandomTip(exclude?: string): string {
  if (GENERATE_TIPS.length <= 1) return GENERATE_TIPS[0] || GENERATE_TIP_FINAL;
  let next = GENERATE_TIPS[Math.floor(Math.random() * GENERATE_TIPS.length)];
  for (let i = 0; i < 10 && next === exclude; i++) {
    next = GENERATE_TIPS[Math.floor(Math.random() * GENERATE_TIPS.length)];
  }
  return next;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error("Failed to read file"));
    reader.readAsDataURL(file);
  });
}

function formatWhen(iso?: string): string {
  if (!iso) return "Unknown time";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m <= 0) return `${s}s`;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

function formatDurationMs(ms?: number | null): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "";
  return formatElapsed(Math.round(ms / 1000));
}

export default function ApplicationsPage() {
  return (
    <RequireAuth pageLabel="Applications">
      <ApplicationsDesk />
    </RequireAuth>
  );
}

function ApplicationsDesk() {
  const formId = useId();
  const [items, setItems] = useState<AppRow[]>([]);
  const [selected, setSelected] = useState<AppDetail | null>(null);
  const [ingestMode, setIngestMode] = useState<IngestMode>("paste");
  const [jd, setJd] = useState("");
  const [jdUrl, setJdUrl] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [selectedTypes, setSelectedTypes] = useState<string[]>(["cover", "email"]);
  const [customType, setCustomType] = useState("");
  const [preview, setPreview] = useState("");
  const [previewTypes, setPreviewTypes] = useState<string[]>([]);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewCreatedAt, setPreviewCreatedAt] = useState("");
  const [previewDurationMs, setPreviewDurationMs] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateClosing, setGenerateClosing] = useState(false);
  const [progressTip, setProgressTip] = useState(GENERATE_TIPS[0]);
  const [generateStartedAt, setGenerateStartedAt] = useState<number | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [status, setStatus] = useState("draft");
  const [notes, setNotes] = useState("");
  const [showNewAppForm, setShowNewAppForm] = useState(false);

  const loadList = useCallback(async () => {
    const json = await apiFetch("/v1/applications");
    setItems((json.items as AppRow[]) || []);
  }, []);

  const clearPreview = useCallback(() => {
    setPreview("");
    setPreviewTypes([]);
    setPreviewId(null);
    setPreviewCreatedAt("");
    setPreviewDurationMs(null);
  }, []);

  const loadDetail = useCallback(
    async (id: string, opts?: { keepPreview?: boolean }) => {
      const json = await apiFetch(`/v1/applications/${encodeURIComponent(id)}`);
      const app = json.application as AppDetail;
      setSelected(app);
      setStatus(app.status || "draft");
      setNotes(app.notes || "");
      if (!opts?.keepPreview) clearPreview();
      setShowNewAppForm(false);
    },
    [clearPreview],
  );

  function clearSelection() {
    setSelected(null);
    clearPreview();
    setShowNewAppForm(false);
  }

  function resetIngestFields() {
    setJd("");
    setJdUrl("");
    setUploadFile(null);
    setIngestMode("paste");
  }

  function toggleType(id: string) {
    setSelectedTypes((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id],
    );
  }

  useEffect(() => {
    loadList().catch((err) => setError(err instanceof Error ? err.message : "Load failed"));
  }, [loadList]);

  useEffect(() => {
    if (!generating || !generateStartedAt) {
      setElapsedSec(0);
      return;
    }
    const tick = () => setElapsedSec(Math.floor((Date.now() - generateStartedAt) / 1000));
    tick();
    const id = window.setInterval(tick, 250);
    return () => window.clearInterval(id);
  }, [generating, generateStartedAt]);

  useEffect(() => {
    if (!generating || generateClosing) return;
    setProgressTip(pickRandomTip());
    const id = window.setInterval(() => {
      setProgressTip((prev) => pickRandomTip(prev));
    }, TIP_ROTATE_MS);
    return () => window.clearInterval(id);
  }, [generating, generateClosing]);

  async function ingest(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      let body: Record<string, string>;
      if (ingestMode === "paste") {
        body = { source_type: "paste", text: jd };
      } else if (ingestMode === "url") {
        body = { source_type: "url", url: jdUrl.trim() };
      } else {
        if (!uploadFile) throw new Error("Choose a file to upload");
        const content_base64 = await fileToBase64(uploadFile);
        body = {
          source_type: "upload",
          filename: uploadFile.name,
          content_base64,
        };
      }
      const json = await apiFetch("/v1/jd/ingest", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const app = json.application as AppDetail;
      await loadList();
      await loadDetail(app.id);
      resetIngestFields();
      setShowNewAppForm(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingest failed");
    } finally {
      setBusy(false);
    }
  }

  async function deleteApp(item: AppRow) {
    const label = `${item.company || "Company"}: ${item.title || "Role"}`;
    if (!window.confirm(`Remove application "${label}"? This deletes its JD and all generations.`)) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await apiFetch(`/v1/applications/${encodeURIComponent(item.id)}/delete`, {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      });
      if (selected?.id === item.id) clearSelection();
      await loadList();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Remove failed");
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!selected) return;
    const typeList = [...selectedTypes];
    const custom = customType.trim();
    if (!typeList.length && !custom) {
      setError("Pick at least one draft type, or enter a custom request.");
      return;
    }
    setBusy(true);
    setGenerating(true);
    setGenerateClosing(false);
    setProgressTip(pickRandomTip());
    setGenerateStartedAt(Date.now());
    setError("");
    clearPreview();
    let nextPreview = "";
    let nextTypes: string[] = typeList;
    let nextId: string | null = null;
    let nextCreatedAt = "";
    let nextDurationMs: number | null = null;
    let succeeded = false;
    try {
      const json = await apiFetch("/v1/generate", {
        method: "POST",
        body: JSON.stringify({
          application_id: selected.id,
          types: typeList,
          custom_type: custom || undefined,
        }),
      });
      const genMeta = json.generation as
        | { id?: string; types?: string[]; createdAt?: string; durationMs?: number | null }
        | undefined;
      const genId = String(genMeta?.id || "");
      nextTypes = Array.isArray(genMeta?.types) ? genMeta.types.map(String) : typeList;
      nextCreatedAt = String(genMeta?.createdAt || "");
      nextDurationMs =
        typeof genMeta?.durationMs === "number" ? genMeta.durationMs : null;
      if (genId) {
        const full = await apiFetch(
          `/v1/applications/${encodeURIComponent(selected.id)}/generations/${encodeURIComponent(genId)}`,
        );
        const fullGen = full.generation as { durationMs?: number | null } | undefined;
        await loadDetail(selected.id, { keepPreview: true });
        nextPreview = String(full.content || "");
        nextId = genId;
        nextDurationMs =
          typeof fullGen?.durationMs === "number"
            ? fullGen.durationMs
            : nextDurationMs;
      } else {
        await loadDetail(selected.id, { keepPreview: true });
        nextPreview = String(json.preview || "");
      }
      succeeded = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generate failed");
    } finally {
      setBusy(false);
      setGenerateClosing(true);
      setProgressTip(GENERATE_TIP_FINAL);
      await sleep(TIP_FINAL_HOLD_MS);
      if (succeeded) {
        setPreview(nextPreview);
        setPreviewTypes(nextTypes);
        setPreviewId(nextId);
        setPreviewCreatedAt(nextCreatedAt);
        setPreviewDurationMs(nextDurationMs);
      }
      setGenerating(false);
      setGenerateClosing(false);
      setGenerateStartedAt(null);
    }
  }

  async function approve(generationId: string) {
    if (!selected) return;
    if (!window.confirm("Approve this generation? Only approve text you are willing to send.")) return;
    setBusy(true);
    setError("");
    try {
      await apiFetch("/v1/generations/approve", {
        method: "POST",
        body: JSON.stringify({
          application_id: selected.id,
          generation_id: generationId,
          confirm: true,
        }),
      });
      const full = await apiFetch(
        `/v1/applications/${encodeURIComponent(selected.id)}/generations/${encodeURIComponent(generationId)}`,
      );
      const fullGen = full.generation as { durationMs?: number | null } | undefined;
      const gen = (selected.generations || []).find((g) => g.id === generationId);
      setPreview(String(full.content || ""));
      setPreviewTypes(gen?.types || previewTypes);
      setPreviewId(generationId);
      setPreviewCreatedAt(gen?.createdAt || "");
      setPreviewDurationMs(
        typeof fullGen?.durationMs === "number"
          ? fullGen.durationMs
          : typeof gen?.durationMs === "number"
            ? gen.durationMs
            : null,
      );
      await loadDetail(selected.id, { keepPreview: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approve failed");
    } finally {
      setBusy(false);
    }
  }

  async function deleteGeneration(generationId: string) {
    if (!selected) return;
    if (!window.confirm(`Delete this generation? This cannot be undone.`)) return;
    setBusy(true);
    setError("");
    try {
      await apiFetch("/v1/generations/delete", {
        method: "POST",
        body: JSON.stringify({
          application_id: selected.id,
          generation_id: generationId,
          confirm: true,
        }),
      });
      if (previewId === generationId) clearPreview();
      await loadList();
      await loadDetail(selected.id, { keepPreview: previewId !== generationId });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  async function saveMeta() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await apiFetch("/v1/applications", {
        method: "POST",
        body: JSON.stringify({ id: selected.id, status, notes }),
      });
      await loadList();
      await loadDetail(selected.id, { keepPreview: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function openGeneration(generationId: string) {
    if (!selected) return;
    try {
      const full = await apiFetch(
        `/v1/applications/${encodeURIComponent(selected.id)}/generations/${encodeURIComponent(generationId)}`,
      );
      const fullGen = full.generation as { durationMs?: number | null } | undefined;
      const gen = (selected.generations || []).find((g) => g.id === generationId);
      setPreview(String(full.content || ""));
      setPreviewTypes(gen?.types || []);
      setPreviewId(generationId);
      setPreviewCreatedAt(gen?.createdAt || "");
      setPreviewDurationMs(
        typeof fullGen?.durationMs === "number"
          ? fullGen.durationMs
          : typeof gen?.durationMs === "number"
            ? gen.durationMs
            : null,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Load generation failed");
    }
  }

  const hasApps = items.length > 0;
  const hasSelection = Boolean(selected);
  const showIngest = !hasSelection || showNewAppForm;
  const tip = progressTip;
  const canGenerate = selectedTypes.length > 0 || Boolean(customType.trim());

  function renderAppList(opts: { showActive?: boolean }) {
    return (
      <ul className="list">
        {items.map((item) => {
          const active = Boolean(opts.showActive && selected?.id === item.id);
          return (
            <li key={item.id} className="row-card" data-active={active}>
              <button
                type="button"
                className="row-card-main"
                onClick={() => loadDetail(item.id).catch((err) => setError(String(err)))}
              >
                <strong>
                  {item.company || "Company"}: {item.title || "Role"}
                </strong>
                <span className="muted">
                  <span className="status">{item.status || "draft"}</span> · {item.id}
                </span>
              </button>
              <div className="row-card-actions">
                <button
                  type="button"
                  className="icon-btn danger"
                  disabled={busy || generating}
                  title="Remove application"
                  aria-label={`Remove ${item.company || "application"}`}
                  onClick={() => deleteApp(item)}
                >
                  <TrashIcon />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    );
  }

  const ingestForm = (
    <form onSubmit={ingest}>
      <div className="source-tabs" role="tablist" aria-label="How to add a job description">
        {(
          [
            ["paste", "Paste text"],
            ["url", "From URL"],
            ["upload", "Upload file"],
          ] as const
        ).map(([mode, label]) => (
          <button
            key={mode}
            type="button"
            role="tab"
            aria-selected={ingestMode === mode}
            data-active={ingestMode === mode}
            onClick={() => setIngestMode(mode)}
          >
            {label}
          </button>
        ))}
      </div>

      {ingestMode === "paste" ? (
        <div className="field">
          <label htmlFor={`${formId}-jd`}>Job description text</label>
          <textarea
            id={`${formId}-jd`}
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            placeholder="Paste the full JD here…"
            required
          />
        </div>
      ) : null}

      {ingestMode === "url" ? (
        <div className="field">
          <label htmlFor={`${formId}-url`}>Job posting URL</label>
          <input
            id={`${formId}-url`}
            type="url"
            value={jdUrl}
            onChange={(e) => setJdUrl(e.target.value)}
            placeholder="https://…"
            required
          />
          <p className="muted" style={{ margin: 0 }}>
            Some boards block automated fetches. If that happens, paste the text or upload a
            saved page instead.
          </p>
        </div>
      ) : null}

      {ingestMode === "upload" ? (
        <div className="field">
          <label htmlFor={`${formId}-file`}>JD file (.txt, .md, .html, .pdf, .docx, .doc)</label>
          <input
            id={`${formId}-file`}
            type="file"
            accept=".txt,.md,.markdown,.html,.htm,.pdf,.docx,.doc,text/plain,text/html,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword"
            onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
            required
          />
          {uploadFile ? (
            <p className="muted" style={{ margin: 0 }}>
              Selected: {uploadFile.name}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="actions">
        <button className="btn" type="submit" disabled={busy || generating}>
          {hasApps ? "Save new application" : "Save and open it"}
        </button>
        {hasSelection && showNewAppForm ? (
          <button
            className="btn secondary"
            type="button"
            onClick={() => {
              setShowNewAppForm(false);
              resetIngestFields();
            }}
          >
            Cancel
          </button>
        ) : null}
      </div>
    </form>
  );

  return (
    <>
      <section className="hero compact">
        <div className="eyebrow">Applications</div>
        <h1>From posting to send-ready draft.</h1>
        <p>
          Choose or create an application, then generate materials from your knowledge bank.
          Approve only what you are willing to send.
        </p>
      </section>

      {error ? <p className="error">{error}</p> : null}

      <div className="stack workbench">
        <div className="howto">
          <h2>How to use this page</h2>
          <p>Two moves. Step 2 stays hidden until an application is selected.</p>
          <ol className="steps">
            <li>
              <span className="step-num">1</span>
              <div>
                <strong>Add or choose an application</strong>
                <span>
                  Paste a JD, fetch a URL, or upload a file, or click an existing row. Clear or
                  switch later from here.
                </span>
              </div>
            </li>
            <li>
              <span className="step-num">2</span>
              <div>
                <strong>Generate, review, approve</strong>
                <span>Appears only after you select an application.</span>
              </div>
            </li>
          </ol>
        </div>

        <div className={`panel ${!hasSelection ? "start-here" : ""}`.trim()}>
          <div className="panel-title">
            <h2>1. Add or choose an application</h2>
            {!hasSelection ? <span className="badge-soft">Do this first</span> : null}
            {hasSelection ? <span className="badge-soft">Selected</span> : null}
          </div>

          {hasSelection ? (
            <>
              <p className="panel-lead">
                Working on <strong>{selected?.company || "Company"}</strong>:{" "}
                {selected?.title || "Role"}. Draft tools are in step 2 below. Use the buttons here
                to switch, clear, or start a new application.
              </p>
              <div className="actions" style={{ marginBottom: "0.85rem" }}>
                <button className="btn secondary" type="button" onClick={clearSelection}>
                  Clear selection
                </button>
                <button
                  className="btn secondary"
                  type="button"
                  onClick={() => setShowNewAppForm((v) => !v)}
                >
                  {showNewAppForm ? "Hide new application form" : "Add another application"}
                </button>
              </div>
              {showIngest ? (
                <>
                  <p className="panel-lead">
                    Paste, fetch a URL, or upload a file to create another application.
                  </p>
                  {ingestForm}
                </>
              ) : null}
              {hasApps ? (
                <>
                  <p className="panel-lead" style={{ marginTop: "0.75rem" }}>
                    Or click a different row to switch:
                  </p>
                  {renderAppList({ showActive: true })}
                </>
              ) : null}
            </>
          ) : (
            <>
              <p className="panel-lead">
                {hasApps
                  ? "Add a JD below (paste, URL, or upload), or click an existing application to open drafting."
                  : "Add a job posting (paste, URL, or upload) to create your first application. Step 2 unlocks after that."}
              </p>

              {showIngest ? (
                <div style={{ marginBottom: "1rem" }}>{ingestForm}</div>
              ) : null}

              {hasApps ? (
                <>
                  <p className="panel-lead">Existing applications. Click one to continue:</p>
                  {renderAppList({})}
                </>
              ) : (
                <div className="empty-hint">No applications yet. The form above is your start.</div>
              )}
            </>
          )}
        </div>

        {hasSelection ? (
          <div className="panel">
            <div className="panel-title">
              <h2>2. Generate, review, and approve</h2>
              <span className="badge-soft">Active</span>
            </div>
            <p className="panel-lead">
              Drafts are grounded in your Knowledge bank. They stay drafts until you approve them.
            </p>
            <p className="muted">{selected?.location || "Location unknown"}</p>
            <div className="field">
              <label htmlFor={`${formId}-status`}>Application status</label>
              <select
                id={`${formId}-status`}
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                {["draft", "applied", "interview", "offer", "rejected", "withdrawn"].map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor={`${formId}-notes`}>Private notes</label>
              <textarea
                id={`${formId}-notes`}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Reminders for yourself."
              />
            </div>
            <div className="actions">
              <button
                className="btn secondary"
                type="button"
                onClick={saveMeta}
                disabled={busy || generating}
              >
                Save status and notes
              </button>
            </div>

            <div className="field" style={{ marginTop: "1rem" }}>
              <label>What should we draft?</label>
              <p className="muted" style={{ margin: "0 0 0.55rem" }}>
                Pick one or more fixed types. Use Other only for a custom request.
              </p>
              <div className="type-chips" role="group" aria-label="Draft types">
                {ARTIFACTS.map((a) => (
                  <button
                    key={a.id}
                    type="button"
                    className="type-chip"
                    data-active={selectedTypes.includes(a.id)}
                    aria-pressed={selectedTypes.includes(a.id)}
                    disabled={generating}
                    onClick={() => toggleType(a.id)}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label htmlFor={`${formId}-custom`}>Other (optional custom request)</label>
              <input
                id={`${formId}-custom`}
                value={customType}
                onChange={(e) => setCustomType(e.target.value)}
                placeholder="e.g. thank-you note after interview"
                disabled={generating}
              />
            </div>
            <div className="actions">
              <button
                className="btn"
                type="button"
                onClick={generate}
                disabled={busy || generating || !canGenerate}
              >
                {generating ? "Generating…" : "Generate draft"}
              </button>
            </div>

            {generating ? (
              <div className="generate-progress" role="status" aria-live="polite">
                <div className="generate-progress-head">
                  <span className="generate-spinner" aria-hidden="true" />
                  <strong>Generating your draft</strong>
                  <span className="badge-soft">{formatElapsed(elapsedSec)}</span>
                </div>
                <p className="generate-progress-tip">{tip}</p>
                <div className="generate-progress-bar" aria-hidden="true">
                  <span />
                </div>
                <p className="muted" style={{ margin: "0.55rem 0 0", fontSize: "0.85rem" }}>
                  You can keep this tab open. All-type runs use several model batches and can take
                  10–20+ minutes on a local LLM.
                </p>
              </div>
            ) : null}

            <h2 style={{ marginTop: "1.25rem" }}>Saved generations</h2>
            <p className="panel-lead">
              Click a row to preview it. Approve only text you are willing to send.
            </p>
            {(selected?.generations || []).length ? (
              <ul className="list">
                {[...(selected?.generations || [])].reverse().map((g) => (
                  <li key={g.id} className="row-card" data-active={previewId === g.id}>
                    <button
                      type="button"
                      className="row-card-main"
                      onClick={() => openGeneration(g.id)}
                    >
                      <strong>{formatWhen(g.createdAt)}</strong>
                      <span className="muted">
                        <span className="status">{g.approval || "draft"}</span>
                        {(g.types || []).length ? ` · ${(g.types || []).join(", ")}` : ""}
                        {formatDurationMs(g.durationMs)
                          ? ` · took ${formatDurationMs(g.durationMs)}`
                          : ""}
                      </span>
                    </button>
                    <div className="row-card-actions">
                      {g.approval !== "approved" ? (
                        <button
                          className="btn secondary row-card-btn"
                          type="button"
                          disabled={busy || generating}
                          onClick={() => approve(g.id)}
                        >
                          Approve
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="icon-btn danger"
                        disabled={busy || generating}
                        title="Delete generation"
                        aria-label={`Delete generation from ${formatWhen(g.createdAt)}`}
                        onClick={() => deleteGeneration(g.id)}
                      >
                        <TrashIcon />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="empty-hint">No generations yet. Choose types and generate.</div>
            )}

            {preview ? (
              <>
                <div className="panel-title" style={{ marginTop: "1rem" }}>
                  <h2>Preview</h2>
                  <button className="btn secondary" type="button" onClick={clearPreview}>
                    Clear preview
                  </button>
                </div>
                <p className="panel-lead">
                  Each draft block can be copied on its own. Emphasis and grounding notes stay
                  collapsed. They are not part of what you send.
                </p>
                <GenerationPreview
                  content={preview}
                  requestedTypes={previewTypes}
                  generationId={previewId || undefined}
                  createdAt={previewCreatedAt || undefined}
                  durationMs={previewDurationMs}
                />
              </>
            ) : null}
          </div>
        ) : (
          <div className="empty-hint">
            Step 2 (generate and approve) appears here after you select or create an application
            above.
          </div>
        )}
      </div>
    </>
  );
}
