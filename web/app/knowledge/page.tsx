"use client";

import { FormEvent, useCallback, useEffect, useId, useState } from "react";
import Link from "next/link";
import { RequireAuth } from "@/components/RequireAuth";
import { TrashIcon } from "@/components/TrashIcon";
import { apiFetch } from "@/lib/api";

type Exp = { id?: string; org?: string; role?: string; dates?: string; points?: string[] };
type Metric = { id?: string; text?: string; tags?: string[] };
type Blurb = { id?: string; label?: string; text?: string; tags?: string[] };
type Story = { id?: string; title?: string; body?: string; tags?: string[] };
type Profile = {
  name?: string;
  location?: string;
  email?: string;
  shortName?: string;
  [key: string]: unknown;
};
type Truth = {
  headline?: string;
  profile?: Profile;
  voice?: { notes?: string; punctuation?: string };
  metrics?: Metric[];
  blurbs?: Blurb[];
  neverClaim?: string[];
  experience?: Exp[];
  signatureStories?: Story[];
  careerFit?: {
    industries?: { name?: string; rank?: number; rationale?: string }[];
    titles?: { name?: string; rank?: number; rationale?: string; industry?: string }[];
    confidence?: string;
    notes?: string;
    acceptedAt?: string;
  };
};

export default function KnowledgePage() {
  return (
    <RequireAuth pageLabel="Knowledge">
      <KnowledgeDesk />
    </RequireAuth>
  );
}

function KnowledgeDesk() {
  const formId = useId();
  const [truth, setTruth] = useState<Truth | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [headline, setHeadline] = useState("");
  const [location, setLocation] = useState("");
  const [email, setEmail] = useState("");
  const [blurbText, setBlurbText] = useState("");
  const [blurbLabel, setBlurbLabel] = useState("");
  const [metricText, setMetricText] = useState("");
  const [neverText, setNeverText] = useState("");
  const [storyTitle, setStoryTitle] = useState("");
  const [storyBody, setStoryBody] = useState("");
  const [expOrg, setExpOrg] = useState("");
  const [expRole, setExpRole] = useState("");
  const [expDates, setExpDates] = useState("");
  const [expPoints, setExpPoints] = useState("");

  const load = useCallback(async () => {
    const json = await apiFetch("/v1/truth");
    const t = json.truth as Truth;
    setTruth(t);
    setCounts((json.counts as Record<string, number>) || {});
    setHeadline(t.headline || "");
    setLocation(t.profile?.location || "");
    setEmail(t.profile?.email || "");
  }, []);

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : "Load failed"));
  }, [load]);

  async function run(op: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await op();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  async function saveProfileBasics(e: FormEvent) {
    e.preventDefault();
    await run(() =>
      apiFetch("/v1/truth", {
        method: "PATCH",
        body: JSON.stringify({
          op: "set",
          section: "profile",
          value: {
            ...(truth?.profile || {}),
            location: location.trim(),
            email: email.trim(),
          },
        }),
      }),
    );
  }

  async function saveHeadline(e: FormEvent) {
    e.preventDefault();
    await run(() =>
      apiFetch("/v1/truth", {
        method: "PATCH",
        body: JSON.stringify({ op: "set", section: "headline", value: headline }),
      }),
    );
  }

  async function addBlurb(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await apiFetch("/v1/truth", {
        method: "PATCH",
        body: JSON.stringify({
          op: "add",
          section: "blurbs",
          item: { text: blurbText, label: blurbLabel || "Blurb" },
        }),
      });
      setBlurbText("");
      setBlurbLabel("");
    });
  }

  async function addMetric(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await apiFetch("/v1/truth", {
        method: "PATCH",
        body: JSON.stringify({ op: "add", section: "metrics", item: { text: metricText } }),
      });
      setMetricText("");
    });
  }

  async function addNever(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await apiFetch("/v1/truth", {
        method: "PATCH",
        body: JSON.stringify({ op: "add", section: "neverClaim", item: neverText }),
      });
      setNeverText("");
    });
  }

  async function addStory(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await apiFetch("/v1/truth", {
        method: "PATCH",
        body: JSON.stringify({
          op: "add",
          section: "signatureStories",
          item: { title: storyTitle, body: storyBody },
        }),
      });
      setStoryTitle("");
      setStoryBody("");
    });
  }

  async function addExperience(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await apiFetch("/v1/truth", {
        method: "PATCH",
        body: JSON.stringify({
          op: "add",
          section: "experience",
          item: {
            org: expOrg,
            role: expRole,
            dates: expDates,
            points: expPoints
              .split("\n")
              .map((p) => p.trim())
              .filter(Boolean),
          },
        }),
      });
      setExpOrg("");
      setExpRole("");
      setExpDates("");
      setExpPoints("");
    });
  }

  async function removeItem(section: string, itemId?: string, index?: number, label?: string) {
    const what = label ? `“${label}”` : "this entry";
    if (!window.confirm(`Remove ${what}?`)) return;
    await run(() =>
      apiFetch("/v1/truth", {
        method: "PATCH",
        body: JSON.stringify({
          op: "remove",
          section,
          item_id: itemId,
          index,
          confirm: true,
        }),
      }),
    );
  }

  function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
    return (
      <button
        type="button"
        className="icon-btn danger"
        disabled={busy}
        title={`Remove ${label}`}
        aria-label={`Remove ${label}`}
        onClick={onClick}
      >
        <TrashIcon />
      </button>
    );
  }

  return (
    <>
      <section className="hero compact">
        <div className="eyebrow">Knowledge bank</div>
        <h1>Build the facts drafts can use.</h1>
        <p>
          Everything generated on Applications is grounded only in this bank. Put in what you will
          defend; put never-claims for anything the model must not invent.
        </p>
      </section>

      {error ? <p className="error">{error}</p> : null}

      {(truth?.careerFit?.industries || []).length ? (
        <section className="panel fit-accepted">
          <div className="panel-title">
            <h2>Accepted career fit</h2>
            <span className="muted">
              from interview · edit via <Link href="/fit">Career fit</Link>
            </span>
          </div>
          <p className="panel-lead">
            Confidence {truth?.careerFit?.confidence || "medium"}
            {truth?.careerFit?.acceptedAt ? ` · accepted ${truth.careerFit.acceptedAt}` : ""}.
          </p>
          <ul className="list">
            {(truth?.careerFit?.industries || []).map((i) => (
              <li key={`ind-${i.rank}-${i.name}`}>
                Industry #{i.rank}: {i.name}
                {i.rationale ? `: ${i.rationale}` : ""}
              </li>
            ))}
            {(truth?.careerFit?.titles || []).map((t) => (
              <li key={`title-${t.rank}-${t.name}`}>
                Title #{t.rank}: {t.name}
                {t.industry ? ` (${t.industry})` : ""}
                {t.rationale ? `: ${t.rationale}` : ""}
              </li>
            ))}
          </ul>
          {truth?.careerFit?.notes ? <p className="muted">{truth.careerFit.notes}</p> : null}
        </section>
      ) : null}

      <div className="stack workbench">
        <div className="howto">
          <h2>How to use this page</h2>
          <p>
            Fill this before you generate. Start with a headline, add roles and metrics you can
            prove, then add blurbs or stories you reuse often.
          </p>
          <ol className="steps steps-3">
            <li>
              <span className="step-num">1</span>
              <div>
                <strong>Set your headline</strong>
                <span>One accurate line that describes how you want to be introduced.</span>
              </div>
            </li>
            <li>
              <span className="step-num">2</span>
              <div>
                <strong>Add roles and metrics</strong>
                <span>Only facts and numbers you can stand behind in an interview.</span>
              </div>
            </li>
            <li>
              <span className="step-num">3</span>
              <div>
                <strong>Guardrails and voice</strong>
                <span>Blurbs, stories, and never-claims steer tone and hard stops.</span>
              </div>
            </li>
          </ol>
        </div>

        <div className="panel start-here">
          <div className="panel-title">
            <h2>1. Profile and headline</h2>
            <span className="badge-soft">Start here</span>
          </div>
          <p className="panel-lead">
            This is the first thing to get right. Generations treat your headline as the short
            version of who you are. Location and email can change per venture or target region.
          </p>
          <div className="section-head">
            <h2 style={{ fontSize: "1.1rem" }}>{truth?.profile?.name || "Your name"}</h2>
            <div className="chips">
              <span className="chip">{counts.experience || 0} experience</span>
              <span className="chip">{counts.metrics || 0} metrics</span>
              <span className="chip">{counts.blurbs || 0} blurbs</span>
              <span className="chip">{counts.signatureStories || 0} stories</span>
              <span className="chip">{counts.neverClaim || 0} never-claim</span>
            </div>
          </div>
          <form className="profile-contact" onSubmit={saveProfileBasics}>
            <div className="field-grid">
              <div className="field">
                <label htmlFor={`${formId}-location`}>Location / region</label>
                <input
                  id={`${formId}-location`}
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="Greater Boston"
                  disabled={busy}
                />
              </div>
              <div className="field">
                <label htmlFor={`${formId}-email`}>Email</label>
                <input
                  id={`${formId}-email`}
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  disabled={busy}
                />
              </div>
            </div>
            <div className="actions profile-contact-actions">
              <button className="btn secondary" type="submit" disabled={busy}>
                Save location & email
              </button>
            </div>
          </form>
          <form className="profile-headline" onSubmit={saveHeadline}>
            <div className="field">
              <label htmlFor={`${formId}-headline`}>Headline</label>
              <input
                id={`${formId}-headline`}
                value={headline}
                onChange={(e) => setHeadline(e.target.value)}
                placeholder="Short accurate headline | Practice or focus"
                disabled={busy}
              />
            </div>
            <div className="actions">
              <button className="btn" type="submit" disabled={busy}>
                Save headline
              </button>
            </div>
          </form>
        </div>

        <div className="panel">
          <div className="panel-title">
            <h2>2. Experience</h2>
          </div>
          <p className="panel-lead">
            Roles, dates, and bullets the model may cite. Prefer ownership language over vague
            duties.
          </p>
          {(truth?.experience || []).length ? (
            <ul className="list truth-list">
              {(truth?.experience || []).map((exp, idx) => {
                const label = `${exp.org || "Role"}: ${exp.role || ""}`.trim();
                return (
                  <li className="row-card truth-row" key={exp.id || `${exp.org}-${idx}`}>
                    <div className="row-card-main">
                      <strong>{label}</strong>
                      {exp.dates ? <span className="muted">{exp.dates}</span> : null}
                      {(exp.points || []).length ? (
                        <ul className="muted truth-points">
                          {(exp.points || []).map((p) => (
                            <li key={p}>{p}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                    <div className="row-card-actions">
                      <RemoveButton
                        label={label}
                        onClick={() =>
                          removeItem("experience", exp.id, exp.id ? undefined : idx, label)
                        }
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="empty-hint">No roles yet. Open the form below to add your first one.</div>
          )}
          <details className="add-block">
            <summary>Add experience</summary>
            <form onSubmit={addExperience}>
              <div className="field">
                <label htmlFor={`${formId}-org`}>Organization</label>
                <input
                  id={`${formId}-org`}
                  value={expOrg}
                  onChange={(e) => setExpOrg(e.target.value)}
                  required
                  disabled={busy}
                />
              </div>
              <div className="field">
                <label htmlFor={`${formId}-role`}>Role</label>
                <input
                  id={`${formId}-role`}
                  value={expRole}
                  onChange={(e) => setExpRole(e.target.value)}
                  required
                  disabled={busy}
                />
              </div>
              <div className="field">
                <label htmlFor={`${formId}-dates`}>Dates</label>
                <input
                  id={`${formId}-dates`}
                  value={expDates}
                  onChange={(e) => setExpDates(e.target.value)}
                  placeholder="Mar 2021 to present"
                  disabled={busy}
                />
              </div>
              <div className="field">
                <label htmlFor={`${formId}-points`}>Points (one per line)</label>
                <textarea
                  id={`${formId}-points`}
                  value={expPoints}
                  onChange={(e) => setExpPoints(e.target.value)}
                  placeholder={"Owned X\nCut Y from A to B"}
                  disabled={busy}
                />
              </div>
              <div className="actions">
                <button className="btn" type="submit" disabled={busy}>
                  Save experience
                </button>
              </div>
            </form>
          </details>
        </div>

        <div className="panel">
          <div className="panel-title">
            <h2>3. Metrics</h2>
          </div>
          <p className="panel-lead">
            Countable facts only. These are the strongest grounding for tailored drafts.
          </p>
          {(truth?.metrics || []).length ? (
            <ul className="list truth-list">
              {(truth?.metrics || []).map((m, idx) => {
                const label = (m.text || "metric").slice(0, 72);
                return (
                  <li className="row-card truth-row" key={m.id || m.text}>
                    <div className="row-card-main">
                      <span>{m.text}</span>
                    </div>
                    <div className="row-card-actions">
                      <RemoveButton
                        label={label}
                        onClick={() =>
                          removeItem("metrics", m.id, m.id ? undefined : idx, label)
                        }
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="empty-hint">Add at least one metric you can defend out loud.</div>
          )}
          <details className="add-block">
            <summary>Add metric</summary>
            <form onSubmit={addMetric}>
              <div className="field">
                <label htmlFor={`${formId}-metric`}>Metric text</label>
                <textarea
                  id={`${formId}-metric`}
                  value={metricText}
                  onChange={(e) => setMetricText(e.target.value)}
                  placeholder="Cut average PR time-to-prod from 2 days to under 4 hours"
                  required
                  disabled={busy}
                />
              </div>
              <div className="actions">
                <button className="btn" type="submit" disabled={busy}>
                  Save metric
                </button>
              </div>
            </form>
          </details>
        </div>

        <div className="panel">
          <div className="panel-title">
            <h2>4. Blurbs</h2>
          </div>
          <p className="panel-lead">
            Short reusable lines for intros, emails, or emphasis. Keep them factual.
          </p>
          {(truth?.blurbs || []).length ? (
            <ul className="list truth-list">
              {(truth?.blurbs || []).map((b, idx) => {
                const label = b.label || "Blurb";
                return (
                  <li className="row-card truth-row" key={b.id || b.text}>
                    <div className="row-card-main">
                      <strong>{label}</strong>
                      <span>{b.text}</span>
                    </div>
                    <div className="row-card-actions">
                      <RemoveButton
                        label={label}
                        onClick={() =>
                          removeItem("blurbs", b.id, b.id ? undefined : idx, label)
                        }
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="empty-hint">Optional, but useful once you have a few go-to lines.</div>
          )}
          <details className="add-block">
            <summary>Add blurb</summary>
            <form onSubmit={addBlurb}>
              <div className="field">
                <label htmlFor={`${formId}-blurb-label`}>Label</label>
                <input
                  id={`${formId}-blurb-label`}
                  value={blurbLabel}
                  onChange={(e) => setBlurbLabel(e.target.value)}
                  placeholder="Developer experience"
                  disabled={busy}
                />
              </div>
              <div className="field">
                <label htmlFor={`${formId}-blurb`}>Text</label>
                <textarea
                  id={`${formId}-blurb`}
                  value={blurbText}
                  onChange={(e) => setBlurbText(e.target.value)}
                  required
                  disabled={busy}
                />
              </div>
              <div className="actions">
                <button className="btn" type="submit" disabled={busy}>
                  Save blurb
                </button>
              </div>
            </form>
          </details>
        </div>

        <div className="panel">
          <div className="panel-title">
            <h2>5. Signature stories</h2>
          </div>
          <p className="panel-lead">
            Longer narratives the model can draw from for interviews or cover letters.
          </p>
          {(truth?.signatureStories || []).length ? (
            <ul className="list truth-list">
              {(truth?.signatureStories || []).map((s, idx) => {
                const label = s.title || "Story";
                return (
                  <li className="row-card truth-row" key={s.id || s.title}>
                    <div className="row-card-main">
                      <strong>{label}</strong>
                      {s.body ? <span className="muted">{s.body}</span> : null}
                    </div>
                    <div className="row-card-actions">
                      <RemoveButton
                        label={label}
                        onClick={() =>
                          removeItem(
                            "signatureStories",
                            s.id,
                            s.id ? undefined : idx,
                            label,
                          )
                        }
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="empty-hint">Add a story when you have a situation worth retelling.</div>
          )}
          <details className="add-block">
            <summary>Add story</summary>
            <form onSubmit={addStory}>
              <div className="field">
                <label htmlFor={`${formId}-story-title`}>Title</label>
                <input
                  id={`${formId}-story-title`}
                  value={storyTitle}
                  onChange={(e) => setStoryTitle(e.target.value)}
                  required
                  disabled={busy}
                />
              </div>
              <div className="field">
                <label htmlFor={`${formId}-story-body`}>Body</label>
                <textarea
                  id={`${formId}-story-body`}
                  value={storyBody}
                  onChange={(e) => setStoryBody(e.target.value)}
                  required
                  disabled={busy}
                />
              </div>
              <div className="actions">
                <button className="btn" type="submit" disabled={busy}>
                  Save story
                </button>
              </div>
            </form>
          </details>
        </div>

        <div className="panel">
          <div className="panel-title">
            <h2>6. Never claim</h2>
          </div>
          <p className="panel-lead">
            Hard stops. The model must not invent these. Add anything you refuse to overstate.
          </p>
          {(truth?.neverClaim || []).length ? (
            <ul className="list truth-list">
              {(truth?.neverClaim || []).map((n, idx) => {
                const label = n.slice(0, 72);
                return (
                  <li className="row-card truth-row" key={`${n}-${idx}`}>
                    <div className="row-card-main">
                      <span>{n}</span>
                    </div>
                    <div className="row-card-actions">
                      <RemoveButton
                        label={label}
                        onClick={() => removeItem("neverClaim", undefined, idx, label)}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="empty-hint">
              Example: &quot;Do not invent management of people or budget.&quot;
            </div>
          )}
          <details className="add-block">
            <summary>Add never-claim</summary>
            <form onSubmit={addNever}>
              <div className="field">
                <label htmlFor={`${formId}-never`}>Never-claim text</label>
                <input
                  id={`${formId}-never`}
                  value={neverText}
                  onChange={(e) => setNeverText(e.target.value)}
                  required
                  disabled={busy}
                />
              </div>
              <div className="actions">
                <button className="btn" type="submit" disabled={busy}>
                  Save never-claim
                </button>
              </div>
            </form>
          </details>
        </div>
      </div>
    </>
  );
}
