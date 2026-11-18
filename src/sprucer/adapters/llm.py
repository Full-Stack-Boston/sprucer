from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx


class LlmAdapter(Protocol):
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 1800,
        temperature: float = 0.35,
    ) -> str: ...


class OpenAICompatLlm:
    def __init__(self, *, base_url: str, api_key: str, default_model: str) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.default_model = default_model or "qwen-coder"

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 1800,
        temperature: float = 0.35,
    ) -> str:
        if not self.base_url:
            raise RuntimeError("SPRUCER_LLM_URL is not configured")
        if not self.api_key:
            raise RuntimeError("SPRUCER_LLM_API_KEY is not configured")
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # Local Ollama (esp. CPU) can exceed 5m on cold multi-artifact drafts.
        async with httpx.AsyncClient(timeout=900.0, trust_env=False) as client:
            try:
                res = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise RuntimeError(
                    "LLM timed out. Try fewer artifact types, a faster model, or raise the LLM timeout."
                ) from exc
            if res.status_code >= 400:
                raise RuntimeError(f"LLM HTTP {res.status_code}: {res.text[:400]}")
            data = res.json()
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise RuntimeError(f"LLM unexpected response: {str(data)[:300]}") from exc


class MockLlm:
    """Deterministic LLM for tests — no network."""

    def __init__(self, reply: str | None = None) -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 1800,
        temperature: float = 0.35,
    ) -> str:
        self.calls.append(messages)
        if self.reply is not None:
            return self.reply
        types: list[str] = []
        cite_ids: list[str] = []
        for msg in messages:
            if msg.get("role") != "user":
                continue
            raw = msg.get("content") or ""
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("artifactTypes"):
                types = [str(t) for t in payload["artifactTypes"]]
            catalog = payload.get("interviewCiteCatalog") or []
            if isinstance(catalog, list):
                for row in catalog:
                    if isinstance(row, dict) and row.get("id"):
                        cite_ids.append(str(row["id"]))
        if not types:
            types = ["cover", "email"]
        # Career-fit interview (IDEA-16) — JSON contracts for turn / recommend.
        for msg in messages:
            if msg.get("role") != "user":
                continue
            raw = msg.get("content") or ""
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            fit_mode = str(payload.get("fitMode") or "")
            if fit_mode == "turn":
                transcript = payload.get("transcript") or []
                user_turns = [
                    m
                    for m in transcript
                    if isinstance(m, dict) and m.get("role") == "user"
                ]
                ready = len(user_turns) >= 2
                return json.dumps(
                    {
                        "assistantMessage": (
                            "Thanks. That helps. What work style fits you best "
                            "(deep focus vs frequent collaboration), and any geo or remote limits?"
                            if not ready
                            else "I have enough to draft ranked industries and titles. "
                            "Say when to recommend, or add one more constraint."
                        ),
                        "readyForRecommend": ready,
                    },
                    ensure_ascii=True,
                )
            if fit_mode == "recommend":
                spectrum = {}
                vault = payload.get("vaultSummary") if isinstance(payload.get("vaultSummary"), dict) else {}
                if isinstance(vault.get("roleSpectrum"), dict):
                    spectrum = vault["roleSpectrum"]
                summary = str(spectrum.get("summary") or "platform and reliability work")
                return json.dumps(
                    {
                        "industries": [
                            {
                                "name": "Cloud infrastructure / DevOps tooling",
                                "rank": 1,
                                "rationale": f"Grounded in vault spectrum: {summary[:120]}",
                            },
                            {
                                "name": "SaaS developer platform",
                                "rank": 2,
                                "rationale": "Matches ownership of reliability and developer experience.",
                            },
                        ],
                        "titles": [
                            {
                                "name": "Platform Engineer",
                                "rank": 1,
                                "rationale": "Aligns with spectrum preference for platform ownership.",
                                "industry": "Cloud infrastructure / DevOps tooling",
                            },
                            {
                                "name": "SRE / Reliability Engineer",
                                "rank": 2,
                                "rationale": "Reliability ownership called out in vault summary.",
                                "industry": "Cloud infrastructure / DevOps tooling",
                            },
                        ],
                        "constraints": {"geo": "", "remote": "flexible", "other": ""},
                        "confidence": "medium",
                        "notes": "Draft only until you accept into careerFit.",
                    },
                    ensure_ascii=True,
                )
        while len(cite_ids) < 3:
            cite_ids.append(f"fixture-cite-{len(cite_ids)+1}")
        c0, c1, c2 = cite_ids[0], cite_ids[1], cite_ids[2]
        sections: list[str] = []
        for t in types:
            if t == "cover":
                sections.append(
                    "## Cover Letter\n\nI am writing from grounded fixture facts only.\n"
                )
            elif t == "email":
                sections.append(
                    "## Email\n\nSubject: Application\n\nHello,\n\nI am writing about this role.\n\nThanks\n"
                )
            elif t == "resume":
                sections.append(
                    "## Resume\n\n### Experience\n- Built grounded fixture systems end to end.\n"
                )
            elif t == "interview":
                sections.append(
                    "## Interview Prep\n\n"
                    "### Likely questions\n"
                    "1. How do you ship reliable platforms?\n"
                    f"   - Answer with: Lead with ownership of client platforms. Cite: `{c0}`\n"
                    "2. Tell me about a production estate you ran.\n"
                    f"   - Answer with: Point to a concrete metric from the vault. Cite: `{c1}`\n"
                    "3. How do you handle operational ownership?\n"
                    f"   - Answer with: Ground the answer in a real role or project. Cite: `{c2}`\n"
                    "### Refresh from your knowledge bank\n"
                    f"- `{c0}`: vault item\n"
                    f"- `{c1}`: vault item\n"
                    f"- `{c2}`: vault item\n"
                    "### Watch-outs\n"
                    "- Never invent unverified percentage metrics\n"
                )
            elif t == "linkedin":
                sections.append(
                    "## LinkedIn DM\n\n"
                    "Hi team. I build client platforms end to end and saw your role. "
                    "Would you be open to a short chat about fit?\n\n"
                    "Jonaldo\n"
                )
            else:
                label = t.replace("custom:", "").replace("-", " ").title()
                sections.append(f"## {label}\n\nGrounded custom draft from fixture facts.\n")
        return "\n".join(sections)


def _last_user_payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Return the JSON generation payload the service embeds in the user message."""
    for msg in messages:
        if msg.get("role") != "user":
            continue
        try:
            data = json.loads(msg.get("content") or "")
        except Exception:
            continue
        if isinstance(data, dict) and data.get("artifactTypes"):
            return data
    return {}


def _one_line(value: str, *, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    return cleaned[:120] or fallback


def _demo_fact_lines(truth: dict[str, Any]) -> list[str]:
    """Human-readable, grounded facts pulled from the career truth for demo copy."""
    facts: list[str] = []
    for m in truth.get("metrics") or []:
        if isinstance(m, dict) and m.get("text"):
            facts.append(_one_line(str(m["text"]), fallback=""))
    for b in truth.get("blurbs") or []:
        if isinstance(b, dict) and (b.get("text") or b.get("label")):
            facts.append(_one_line(str(b.get("text") or b.get("label")), fallback=""))
    for e in truth.get("experience") or []:
        if isinstance(e, dict):
            role = str(e.get("role") or "").strip()
            org = str(e.get("org") or e.get("employer") or e.get("company") or "").strip()
            joined = " at ".join(x for x in (role, org) if x)
            if joined:
                facts.append(_one_line(joined, fallback=""))
    return [f for f in facts if f]


class DemoLlm:
    """Offline, cost-free generator for demos.

    Produces grounded, presentable drafts entirely from the prompt payload the
    service already assembles (application company/title, career truth facts,
    and the interview cite catalog). Never touches the network, so a demo can
    run with no external providers and no API costs. Output is shaped to satisfy
    ``CareerService._content_satisfies_types`` for every artifact type.
    """

    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 1800,
        temperature: float = 0.35,
    ) -> str:
        self.calls.append(messages)
        payload = _last_user_payload(messages)
        app = payload.get("application") if isinstance(payload.get("application"), dict) else {}
        truth = payload.get("careerTruth") if isinstance(payload.get("careerTruth"), dict) else {}
        types = [str(t) for t in (payload.get("artifactTypes") or [])] or ["cover", "email"]
        company = _one_line(str(app.get("company") or ""), fallback="the company")
        title = _one_line(str(app.get("title") or ""), fallback="the role")
        profile = truth.get("profile") if isinstance(truth.get("profile"), dict) else {}
        name = _one_line(str((profile or {}).get("name") or ""), fallback="Alex Rivera")
        short = _one_line(str((profile or {}).get("shortName") or name.split(" ")[0]), fallback=name)

        facts = _demo_fact_lines(truth) or [
            "I ship reliable systems and document the path from idea to production",
            "I own outcomes end to end and communicate clearly under pressure",
            "I bring transferable operational experience to new tools quickly",
        ]
        fact0 = facts[0]
        fact1 = facts[1] if len(facts) > 1 else facts[0]

        catalog = payload.get("interviewCiteCatalog") or []
        cite_ids = [
            str(r.get("id")) for r in catalog if isinstance(r, dict) and str(r.get("id") or "").strip()
        ]
        while len(cite_ids) < 3:
            cite_ids.append(f"demo-cite-{len(cite_ids) + 1}")
        c0, c1, c2 = cite_ids[0], cite_ids[1], cite_ids[2]

        sections: list[str] = []
        for t in types:
            if t == "cover":
                sections.append(
                    "## Cover Letter\n\n"
                    f"Dear {company} Hiring Team,\n\n"
                    f"I am applying for the {title} role at {company}. {fact0}. "
                    f"{fact1}, which maps directly to what this role needs.\n\n"
                    "I would welcome the chance to discuss how I can help your team.\n\n"
                    f"Sincerely,\n{name}\n"
                )
            elif t == "email":
                sections.append(
                    "## Email\n\n"
                    f"Subject: Application for {title} at {company}\n\n"
                    f"Hello {company} team,\n\n"
                    f"I am reaching out about the {title} role. {fact0}. "
                    "I would love to share how that translates to impact for your team.\n\n"
                    f"Thank you for your time,\n{name}\n"
                )
            elif t == "resume":
                sections.append(
                    "## Resume\n\n"
                    "### Summary\n"
                    f"{title} candidate focused on measurable outcomes for {company}.\n\n"
                    "### Experience\n"
                    f"- {fact0}\n"
                    f"- {fact1}\n\n"
                    "### Skills\n"
                    "- Grounded strictly in the knowledge bank; nothing invented\n"
                )
            elif t == "interview":
                questions = [
                    f"Why do you want to work at {company}?",
                    f"What makes you a fit for the {title} role?",
                    "Tell me about a project you owned end to end?",
                    "How do you handle competing priorities under pressure?",
                    "Describe a time you improved reliability or developer experience?",
                    "How do you bridge gaps when a role needs tools you have not used?",
                ]
                lines = ["## Interview Prep\n", "### Likely questions"]
                for i, q in enumerate(questions):
                    cid = cite_ids[i % 3]
                    lines.append(f"{i + 1}. {q}")
                    lines.append(
                        f"   - Answer with: Ground the answer in a real vault fact for {company}. "
                        f"Cite: `{cid}`"
                    )
                lines.append("### Refresh from your knowledge bank")
                lines.append(f"- `{c0}`: review before the interview")
                lines.append(f"- `{c1}`: review before the interview")
                lines.append(f"- `{c2}`: review before the interview")
                lines.append("### Watch-outs")
                lines.append("- Never claim metrics, tools, or tenure absent from the knowledge bank")
                sections.append("\n".join(lines) + "\n")
            elif t == "linkedin":
                sections.append(
                    "## LinkedIn DM\n\n"
                    f"Hi {company} team. I am exploring the {title} role and think there is a strong "
                    f"fit. {fact0}. Would you be open to a short chat about the team and how I could "
                    f"help? Thanks, {short}\n"
                )
            else:
                label = t.replace("custom:", "").replace("-", " ").title()
                sections.append(
                    f"## {label}\n\n"
                    f"Grounded {label} draft for {company}: {title}. {fact0}.\n"
                )
        return "\n".join(sections)


def make_byok_llm(
    *,
    base_url: str,
    api_key: str,
    model: str | None = None,
    allowed_hosts: set[str] | None = None,
) -> OpenAICompatLlm:
    """Build a per-request LLM from a visitor's own provider credentials.

    The base URL is validated to be a public https endpoint (and optionally on an
    allowlist) so a visitor cannot point Sprucer at internal services.
    """
    from urllib.parse import urlparse

    from sprucer.ssrf import UnsafeUrlError, assert_public_http_url

    base = (base_url or "").strip()
    key = (api_key or "").strip()
    if not base or not key:
        raise ValueError("Both an LLM base URL and API key are required")
    parsed = urlparse(base)
    if parsed.scheme != "https":
        raise ValueError("BYO LLM base URL must use https")
    try:
        assert_public_http_url(base)
    except UnsafeUrlError as exc:
        raise ValueError(str(exc)) from exc
    if allowed_hosts:
        host = (parsed.hostname or "").lower()
        if host not in allowed_hosts:
            raise ValueError(f"Provider host not allowed: {host}")
    return OpenAICompatLlm(base_url=base, api_key=key, default_model=model or "gpt-4o-mini")


async def probe_byok_llm(
    *,
    base_url: str,
    api_key: str,
    model: str | None = None,
    allowed_hosts: set[str] | None = None,
) -> dict[str, str]:
    """Tiny chat/completions probe so a visitor can verify BYO credentials before unlock.

    Uses max_tokens=1. Never logs the API key. Returns provider/model metadata on success.
    """
    llm = make_byok_llm(
        base_url=base_url, api_key=api_key, model=model, allowed_hosts=allowed_hosts
    )
    reply = await llm.chat(
        [{"role": "user", "content": "Reply with the single word: ok"}],
        max_tokens=1,
        temperature=0,
    )
    from urllib.parse import urlparse

    host = (urlparse(llm.base_url).hostname or "").lower()
    return {
        "host": host,
        "model": llm.default_model,
        "preview": (reply or "").strip()[:40],
    }
