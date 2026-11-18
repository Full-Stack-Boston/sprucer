"use client";

import Link from "next/link";
import { ReactNode, useCallback, useEffect, useState } from "react";
import { apiFetch, demoChoice } from "@/lib/api";

export type SessionAuth =
  | { status: "loading" }
  | { status: "open"; mode: string }
  | { status: "signed-in"; subject: string; mode: string }
  | { status: "need-login"; mode: string };

export function useSessionAuth(): SessionAuth {
  const [auth, setAuth] = useState<SessionAuth>({ status: "loading" });

  const refresh = useCallback(async () => {
    try {
      const cfg = (await apiFetch("/v1/auth/config")) as { mode?: string };
      const mode = String(cfg.mode || "dev");
      if (mode === "none") {
        setAuth({ status: "open", mode });
        return;
      }
      try {
        const me = (await apiFetch("/v1/auth/whoami")) as { subject?: string; mode?: string };
        setAuth({
          status: "signed-in",
          subject: String(me.subject || "user"),
          mode: String(me.mode || mode),
        });
      } catch {
        // Demo mode mints an anonymous per-session vault automatically once the
        // visitor has picked a backend at the start gate (offline or BYO).
        if (mode === "demo" && demoChoice()) {
          try {
            await apiFetch("/v1/auth/login", { method: "POST", body: JSON.stringify({}) });
            const me = (await apiFetch("/v1/auth/whoami")) as { subject?: string; mode?: string };
            setAuth({
              status: "signed-in",
              subject: String(me.subject || "demo"),
              mode: String(me.mode || mode),
            });
            return;
          } catch {
            setAuth({ status: "need-login", mode });
            return;
          }
        }
        setAuth({ status: "need-login", mode });
      }
    } catch {
      setAuth({ status: "need-login", mode: "unknown" });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return auth;
}

type Props = {
  children: ReactNode;
  /** Short label for the page being gated, e.g. "Applications". */
  pageLabel: string;
};

/**
 * When auth is enabled and the user has no session, block the page UI and
 * show a login splash. auth_mode=none passes through.
 */
export function RequireAuth({ children, pageLabel }: Props) {
  const auth = useSessionAuth();

  if (auth.status === "loading") {
    return (
      <div className="auth-splash" role="status" aria-live="polite">
        <p className="muted">Checking session…</p>
      </div>
    );
  }

  if (auth.status === "need-login") {
    return (
      <div className="auth-splash" role="alert">
        <section className="hero compact">
          <div className="eyebrow">Sign in required</div>
          <h1>You need to log in</h1>
          <p>
            {pageLabel} is locked until you authenticate. Sign in to open your vault. Without a
            session, nothing here can load or change.
          </p>
        </section>
        <div className="panel auth-splash-panel">
          <p className="panel-lead" style={{ marginTop: 0 }}>
            Your knowledge bank and applications stay empty for anonymous visitors when auth is on.
          </p>
          <div className="actions">
            <Link className="btn" href="/login">
              Go to login
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
