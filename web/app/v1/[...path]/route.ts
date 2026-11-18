import http from "node:http";
import https from "node:https";
import { URL } from "node:url";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
/** Allow long local LLM generates (dev + standalone). Multi-type batches can exceed 10+ min. */
export const maxDuration = 3600;
export const dynamic = "force-dynamic";

const brain = (process.env.SPRUCER_PUBLIC_URL || "http://127.0.0.1:8787").replace(/\/$/, "");
/**
 * Wall-clock wait for brain `/v1/generate`. Must cover multi-batch local Ollama runs
 * (several sequential model calls). This is a Sprucer proxy limit — not Ollama dying.
 */
const PROXY_MS = 3_600_000; // 60 minutes

type UpstreamResult = {
  status: number;
  headers: http.IncomingHttpHeaders;
  body: Buffer;
};

/**
 * Proxy with node:http — global fetch (undici) aborts at 300s waiting for response headers,
 * which surfaces as "Brain proxy failed: fetch failed" on slow generates.
 */
function upstreamRequest(
  target: string,
  opts: {
    method: string;
    headers: Record<string, string>;
    body?: Buffer;
    timeoutMs: number;
  },
): Promise<UpstreamResult> {
  return new Promise((resolve, reject) => {
    const url = new URL(target);
    const lib = url.protocol === "https:" ? https : http;
    const req = lib.request(
      {
        protocol: url.protocol,
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: `${url.pathname}${url.search}`,
        method: opts.method,
        headers: opts.headers,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          resolve({
            status: res.statusCode || 502,
            headers: res.headers,
            body: Buffer.concat(chunks),
          });
        });
        res.on("error", reject);
      },
    );
    req.setTimeout(opts.timeoutMs, () => {
      req.destroy(new Error("Generate timed out waiting for the model"));
    });
    req.on("error", reject);
    if (opts.body && opts.body.length) req.write(opts.body);
    req.end();
  });
}

function pickSetCookies(headers: http.IncomingHttpHeaders): string[] {
  const raw = headers["set-cookie"];
  if (!raw) return [];
  return Array.isArray(raw) ? raw : [raw];
}

async function proxy(req: NextRequest, pathParts: string[]) {
  const target = `${brain}/v1/${pathParts.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = {};
  const cookie = req.headers.get("cookie");
  if (cookie) headers.cookie = cookie;
  const contentType = req.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;
  const authorization = req.headers.get("authorization");
  if (authorization) headers.authorization = authorization;

  const method = req.method.toUpperCase();
  let body: Buffer | undefined;
  if (method !== "GET" && method !== "HEAD") {
    body = Buffer.from(await req.arrayBuffer());
    headers["content-length"] = String(body.length);
  }

  try {
    const upstream = await upstreamRequest(target, {
      method,
      headers,
      body,
      timeoutMs: PROXY_MS,
    });
    const out = new Headers();
    const ct = upstream.headers["content-type"];
    if (typeof ct === "string") out.set("content-type", ct);
    for (const c of pickSetCookies(upstream.headers)) {
      out.append("set-cookie", c);
    }
    return new NextResponse(new Uint8Array(upstream.body), {
      status: upstream.status,
      headers: out,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const timedOut = /timed out/i.test(msg);
    return NextResponse.json(
      {
        detail: timedOut
          ? "Generate timed out waiting for the model (web proxy wait limit). All-type runs on a slow local LLM can take well over 10 minutes. Try again, use fewer artifact types, or a faster model."
          : `Brain proxy failed: ${msg}`,
      },
      { status: timedOut ? 504 : 502 },
    );
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

async function handle(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxy(req, path || []);
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
export const OPTIONS = handle;
