"use client";

import { useEffect, useState } from "react";
import { apiFetch, BYOK_KEYS, DEMO_CHOICE_KEY } from "@/lib/api";

type Config = { demo?: boolean; byok_enabled?: boolean };

/**
 * First-run start gate for demo mode. Blocks the app until the visitor picks a
 * backend:
 *   - "Explore the demo": the offline built-in generator (simulated, no cost).
 *   - "Bring your own AI": prompts for ephemeral OpenAI-compatible credentials
 *     (kept in the browser), verifies them with a 1-token probe, then unlocks
 *     full/real generation.
 * Once a choice is stored the gate never shows again (until they change setup).
 * Renders nothing outside demo mode.
 */
export function DemoGate() {
  const [ready, setReady] = useState(false);
  const [cfg, setCfg] = useState<Config | null>(null);
  const [chosen, setChosen] = useState(false);
  const [view, setView] = useState<"choose" | "byok">("choose");
  const [base, setBase] = useState("");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [error, setError] = useState("");
  const [probeOk, setProbeOk] = useState(false);
  const [probeHost, setProbeHost] = useState("");
  const [probing, setProbing] = useState(false);

  useEffect(() => {
    setChosen(Boolean(window.localStorage.getItem(DEMO_CHOICE_KEY)));
    apiFetch("/v1/config")
      .then((c) => setCfg(c as Config))
      .catch(() => setCfg(null))
      .finally(() => setReady(true));
  }, []);

  if (!ready || !cfg?.demo || chosen) return null;

  const enter = (target: string) => {
    window.location.assign(target);
  };

  const startOffline = () => {
    window.localStorage.setItem(DEMO_CHOICE_KEY, "offline");
    window.localStorage.removeItem(BYOK_KEYS.base);
    window.localStorage.removeItem(BYOK_KEYS.key);
    window.localStorage.removeItem(BYOK_KEYS.model);
    enter("/applications");
  };

  const validateLocal = (): { b: string; k: string } | null => {
    const b = base.trim();
    const k = key.trim();
    if (!b || !k) {
      setError("Enter both a base URL and an API key.");
      return null;
    }
    if (!/^https:\/\//i.test(b)) {
      setError("The base URL must start with https://");
      return null;
    }
    return { b, k };
  };

  const testConnection = async () => {
    const local = validateLocal();
    if (!local) return;
    setProbing(true);
    setError("");
    setProbeOk(false);
    setProbeHost("");
    try {
      const res = (await apiFetch("/v1/llm/probe", {
        method: "POST",
        body: JSON.stringify({
          base_url: local.b,
          api_key: local.k,
          model: model.trim() || null,
        }),
      })) as { host?: string };
      setProbeOk(true);
      setProbeHost(String(res.host || "provider"));
    } catch (err) {
      setProbeOk(false);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setProbing(false);
    }
  };

  const startByok = () => {
    const local = validateLocal();
    if (!local) return;
    if (!probeOk) {
      setError("Test the connection successfully before unlocking.");
      return;
    }
    window.localStorage.setItem(BYOK_KEYS.base, local.b);
    window.localStorage.setItem(BYOK_KEYS.key, local.k);
    window.localStorage.setItem(BYOK_KEYS.model, model.trim());
    window.localStorage.setItem(DEMO_CHOICE_KEY, "byok");
    enter("/applications");
  };

  return (
    <div className="demo-gate" role="dialog" aria-modal="true" aria-label="Choose how to try Sprucer">
      <div className="demo-gate-panel">
        <div className="eyebrow">Welcome</div>
        <h1>Try Sprucer</h1>
        <p className="demo-gate-lead">
          This is a self-contained demo. Your vault is temporary and isolated to this browser
          session. Choose how you want drafts written.
        </p>

        {view === "choose" ? (
          <div className="demo-gate-options">
            <button type="button" className="demo-gate-card" onClick={startOffline}>
              <span className="badge-soft">No setup</span>
              <h2>Explore the demo</h2>
              <p>
                Use the built-in offline generator. Full walkthrough of the workflow with simulated,
                grounded drafts. No account, no API key, no cost.
              </p>
              <span className="demo-gate-cta">Start the demo →</span>
            </button>

            {cfg.byok_enabled ? (
              <button
                type="button"
                className="demo-gate-card"
                onClick={() => {
                  setError("");
                  setProbeOk(false);
                  setView("byok");
                }}
              >
                <span className="badge-soft">Real AI</span>
                <h2>Bring your own AI</h2>
                <p>
                  Connect your own OpenAI-compatible provider for real model output. Your key stays
                  in this browser and is sent only with generate requests, never stored on our
                  server.
                </p>
                <span className="demo-gate-cta">Connect a provider →</span>
              </button>
            ) : null}
          </div>
        ) : (
          <div className="panel demo-gate-form">
            <p className="panel-lead" style={{ marginTop: 0 }}>
              Enter ephemeral credentials for any OpenAI-compatible endpoint. We will send a 1-token
              test before unlocking. Credentials live only in this browser.
            </p>
            <label className="field">
              <span>Base URL (https)</span>
              <input
                type="url"
                placeholder="https://api.openai.com/v1"
                value={base}
                onChange={(e) => {
                  setBase(e.target.value);
                  setProbeOk(false);
                }}
                autoFocus
              />
            </label>
            <label className="field">
              <span>API key</span>
              <input
                type="password"
                placeholder="sk-..."
                value={key}
                onChange={(e) => {
                  setKey(e.target.value);
                  setProbeOk(false);
                }}
              />
            </label>
            <label className="field">
              <span>Model (optional)</span>
              <input
                type="text"
                placeholder="gpt-4o-mini"
                value={model}
                onChange={(e) => {
                  setModel(e.target.value);
                  setProbeOk(false);
                }}
              />
            </label>
            {probeOk ? (
              <p className="demo-gate-ok" role="status">
                Connected to {probeHost}. You can unlock the full demo.
              </p>
            ) : null}
            {error ? (
              <p className="demo-gate-error" role="alert">
                {error}
              </p>
            ) : null}
            <div className="actions">
              <button type="button" className="btn secondary" onClick={() => void testConnection()} disabled={probing}>
                {probing ? "Testing…" : "Test connection"}
              </button>
              <button type="button" className="btn" onClick={startByok} disabled={!probeOk || probing}>
                Unlock full demo
              </button>
              <button type="button" className="btn secondary" onClick={() => setView("choose")}>
                Back
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
