"use client";

import { useEffect, useState } from "react";
import { apiFetch, BYOK_KEYS, DEMO_CHOICE_KEY } from "@/lib/api";

type Config = { demo?: boolean; byok_enabled?: boolean };

/**
 * Persistent status strip shown after the start gate in demo mode: which
 * backend is active, and a way to change the setup (which re-opens the gate).
 * Renders nothing outside demo mode or before a choice is made.
 */
export function DemoBar() {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [choice, setChoice] = useState("");
  const [host, setHost] = useState("");

  useEffect(() => {
    apiFetch("/v1/config")
      .then((c) => setCfg(c as Config))
      .catch(() => setCfg(null));
    setChoice(window.localStorage.getItem(DEMO_CHOICE_KEY) || "");
    const base = window.localStorage.getItem(BYOK_KEYS.base) || "";
    try {
      setHost(base ? new URL(base).host : "");
    } catch {
      setHost("");
    }
  }, []);

  if (!cfg?.demo || !choice) return null;

  const changeSetup = () => {
    window.localStorage.removeItem(DEMO_CHOICE_KEY);
    window.location.assign("/");
  };

  const usingByok = choice === "byok" && host;

  return (
    <div className="demo-bar" role="note">
      <div className="demo-bar-row">
        <span className="badge-soft">Demo</span>
        <p className="demo-bar-text">
          {usingByok ? (
            <>
              Full functionality using your AI provider (<strong>{host}</strong>). Temporary,
              session-isolated vault; your key stays in this browser.
            </>
          ) : (
            <>
              Simulated drafts from the built-in offline generator. No external calls or cost.
              Temporary, session-isolated vault.
            </>
          )}
        </p>
        <button type="button" className="btn secondary" onClick={changeSetup}>
          Change setup
        </button>
      </div>
    </div>
  );
}
