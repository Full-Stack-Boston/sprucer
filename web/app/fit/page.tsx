"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { RequireAuth } from "@/components/RequireAuth";
import { apiFetch } from "@/lib/api";

type FitMessage = { role: string; content: string };

type RankedItem = {
  name: string;
  rank: number;
  rationale?: string;
  industry?: string;
};

type FitDraft = {
  industries?: RankedItem[];
  titles?: RankedItem[];
  constraints?: { geo?: string; remote?: string; other?: string };
  confidence?: string;
  notes?: string;
};

type FitSession = {
  id: string;
  status?: string;
  mode?: string;
  messages?: FitMessage[];
  draft?: FitDraft | null;
  readyForRecommend?: boolean;
  careerFit?: FitDraft & { acceptedAt?: string };
};

export default function FitPage() {
  return (
    <RequireAuth pageLabel="Career fit">
      <FitDesk />
    </RequireAuth>
  );
}

function FitDesk() {
  const [session, setSession] = useState<FitSession | null>(null);
  const [acceptedFit, setAcceptedFit] = useState<FitDraft | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const loadAccepted = useCallback(async () => {
    const json = await apiFetch("/v1/truth");
    const truth = (json.truth || {}) as { careerFit?: FitDraft };
    if (truth.careerFit && Array.isArray(truth.careerFit.industries) && truth.careerFit.industries.length) {
      setAcceptedFit(truth.careerFit);
    } else {
      setAcceptedFit(null);
    }
  }, []);

  useEffect(() => {
    loadAccepted().catch(() => {
      /* ignore — desk still usable */
    });
  }, [loadAccepted]);

  async function run(op: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await op();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  async function startInterview() {
    await run(async () => {
      const json = await apiFetch("/v1/fit/start", { method: "POST", body: "{}" });
      setSession(json.session as FitSession);
    });
  }

  async function sendTurn(e: FormEvent) {
    e.preventDefault();
    if (!session?.id || !message.trim()) return;
    const text = message.trim();
    setMessage("");
    await run(async () => {
      const json = await apiFetch("/v1/fit/turn", {
        method: "POST",
        body: JSON.stringify({ session_id: session.id, message: text }),
      });
      setSession(json.session as FitSession);
    });
  }

  async function recommend() {
    if (!session?.id) return;
    await run(async () => {
      const json = await apiFetch("/v1/fit/recommend", {
        method: "POST",
        body: JSON.stringify({ session_id: session.id }),
      });
      setSession(json.session as FitSession);
    });
  }

  async function acceptDraft() {
    if (!session?.id) return;
    if (!window.confirm("Accept this career-fit draft into your knowledge bank?")) return;
    await run(async () => {
      const json = await apiFetch("/v1/fit/accept", {
        method: "POST",
        body: JSON.stringify({
          session_id: session.id,
          confirm: true,
          update_role_spectrum: true,
        }),
      });
      setSession(json.session as FitSession);
      setAcceptedFit((json.careerFit as FitDraft) || null);
    });
  }

  const draft = session?.draft;
  const interviewing = session && session.status !== "accepted";

  return (
    <>
      <header className="hero compact">
        <h1>Career fit</h1>
        <p>
          AI-guided interview that confirms or learns your interests and constraints, then drafts
          ranked industries and titles. Nothing is written to your vault until you accept. Real job
          postings stay on{" "}
          <Link href="/applications">Applications</Link> via SSRF-guarded ingest. This flow never
          invents employers.
        </p>
      </header>

      {error ? <p className="error">{error}</p> : null}

      {acceptedFit ? (
        <section className="panel fit-accepted">
          <div className="panel-title">
            <h2>Accepted in knowledge bank</h2>
          </div>
          <RankedList title="Industries" items={acceptedFit.industries || []} />
          <RankedList title="Titles / role families" items={acceptedFit.titles || []} />
          {acceptedFit.notes ? <p className="muted">{acceptedFit.notes}</p> : null}
        </section>
      ) : null}

      {!session ? (
        <section className="panel">
          <div className="panel-title">
            <h2>Start interview</h2>
          </div>
          <p className="panel-lead">
            If your vault already has careerFit or a roleSpectrum summary, Sprucer will ask you to
            confirm first. Otherwise it inquires thoroughly. Needs a live LLM (lab{" "}
            <code>SPRUCER_LLM_*</code> or demo BYOK), not the offline demo generator.
          </p>
          <button type="button" className="btn" disabled={busy} onClick={() => void startInterview()}>
            {busy ? "Starting…" : "Start career-fit interview"}
          </button>
        </section>
      ) : (
        <>
          <section className="panel">
            <div className="panel-title">
              <h2>Interview</h2>
              <span className="muted">
                mode {session.mode || "—"} · {session.status || "—"}
              </span>
            </div>
            <div className="fit-transcript" aria-live="polite">
              {(session.messages || []).map((m, i) => (
                <div key={`${m.role}-${i}`} className={`fit-bubble fit-${m.role}`}>
                  <strong>{m.role === "assistant" ? "Sprucer" : "You"}</strong>
                  <p>{m.content}</p>
                </div>
              ))}
            </div>
            {interviewing ? (
              <form className="fit-compose" onSubmit={(e) => void sendTurn(e)}>
                <label className="field">
                  <span>Your reply</span>
                  <textarea
                    rows={3}
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    placeholder="Confirm prefs, correct them, or answer the question…"
                    disabled={busy}
                  />
                </label>
                <div className="fit-actions">
                  <button type="submit" className="btn" disabled={busy || !message.trim()}>
                    {busy ? "Sending…" : "Send"}
                  </button>
                  <button
                    type="button"
                    className="btn secondary"
                    disabled={busy}
                    onClick={() => void recommend()}
                  >
                    Draft industries + titles
                  </button>
                  <button
                    type="button"
                    className="btn secondary"
                    disabled={busy}
                    onClick={() => void startInterview()}
                  >
                    Restart
                  </button>
                </div>
              </form>
            ) : (
              <p className="muted">
                This session is accepted. Start a new interview to revise recommendations.
              </p>
            )}
          </section>

          {draft && Array.isArray(draft.industries) ? (
            <section className="panel">
              <div className="panel-title">
                <h2>Draft recommendations</h2>
                <span className="muted">confidence {draft.confidence || "medium"} · not in vault yet</span>
              </div>
              <RankedList title="Industries" items={draft.industries || []} />
              <RankedList title="Titles / role families" items={draft.titles || []} />
              {draft.constraints &&
              (draft.constraints.geo || draft.constraints.remote || draft.constraints.other) ? (
                <p className="muted">
                  Constraints:{" "}
                  {[draft.constraints.geo, draft.constraints.remote, draft.constraints.other]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              ) : null}
              {draft.notes ? <p>{draft.notes}</p> : null}
              {session.status !== "accepted" ? (
                <button type="button" className="btn" disabled={busy} onClick={() => void acceptDraft()}>
                  Accept into knowledge bank
                </button>
              ) : null}
            </section>
          ) : null}
        </>
      )}
    </>
  );
}

function RankedList({ title, items }: { title: string; items: RankedItem[] }) {
  if (!items.length) {
    return (
      <div className="fit-rank-block">
        <h3>{title}</h3>
        <p className="muted">None yet.</p>
      </div>
    );
  }
  return (
    <div className="fit-rank-block">
      <h3>{title}</h3>
      <ol className="fit-rank-list">
        {items.map((item) => (
          <li key={`${item.rank}-${item.name}`}>
            <strong>{item.name}</strong>
            {item.industry ? <span className="muted"> · {item.industry}</span> : null}
            {item.rationale ? <p className="muted">{item.rationale}</p> : null}
          </li>
        ))}
      </ol>
    </div>
  );
}
