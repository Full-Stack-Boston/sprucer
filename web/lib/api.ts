export function apiBase(): string {
  // Browser: same-origin via Next.js rewrites (keeps session cookies reliable).
  if (typeof window !== "undefined") {
    return "";
  }
  return (
    process.env.SPRUCER_PUBLIC_URL ||
    process.env.NEXT_PUBLIC_SPRUCER_API_URL ||
    "http://127.0.0.1:8787"
  );
}

export const BYOK_KEYS = {
  base: "sprucer-byok-base",
  key: "sprucer-byok-key",
  model: "sprucer-byok-model",
} as const;

/** Which demo backend the visitor chose at the start gate: "offline" | "byok". */
export const DEMO_CHOICE_KEY = "sprucer-demo-choice";

export function demoChoice(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(DEMO_CHOICE_KEY) || "";
}

export async function apiFetch(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  // Bring-your-own-key: forward the visitor's own provider on generate and fit calls.
  // Credentials live in the browser (localStorage) and are never persisted server-side.
  if (
    typeof window !== "undefined" &&
    (path === "/v1/generate" || path.startsWith("/v1/fit/"))
  ) {
    const base = window.localStorage.getItem(BYOK_KEYS.base) || "";
    const key = window.localStorage.getItem(BYOK_KEYS.key) || "";
    const model = window.localStorage.getItem(BYOK_KEYS.model) || "";
    if (base && key) {
      headers.set("X-LLM-Base-Url", base);
      headers.set("X-LLM-Api-Key", key);
      if (model) headers.set("X-LLM-Model", model);
    }
  }
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  const text = await res.text();
  let json: Record<string, unknown> = {};
  try {
    json = text.trim() ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch {
    const snippet = text.replace(/\s+/g, " ").trim().slice(0, 80);
    throw new Error(
      `Non-JSON response from API (${res.status})${snippet ? `: ${snippet}` : ""}. ` +
        (res.status >= 500
          ? "The web proxy or brain may have timed out. Try Generate again."
          : "Check that the Sprucer brain is running."),
    );
  }
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("You need to log in to use this.");
    }
    throw new Error(String(json.detail || json.error || `HTTP ${res.status}`));
  }
  return json;
}
