from __future__ import annotations

import hashlib
import html
import json
import re
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import httpx

from sprucer.adapters.llm import DemoLlm, LlmAdapter
from sprucer.adapters.storage.sqlalchemy_store import SqlAlchemyStorage
from sprucer.tenancy import SHARED_OWNER, owner_key

ARTIFACT_TYPES = ("cover", "resume", "email", "interview", "linkedin")
ARTIFACT_HEADINGS: dict[str, tuple[str, ...]] = {
    "cover": ("cover letter", "cover"),
    "resume": ("resume", "tailored resume"),
    "email": ("email", "email pitch"),
    "interview": ("interview prep", "interview", "interview brief"),
    "linkedin": ("linkedin dm", "linkedin message", "linkedin", "outreach dm", "outreach"),
}
LIST_SECTIONS = {
    "signatureStories",
    "blurbs",
    "metrics",
    "experience",
    "projects",
    "neverClaim",
}


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _slug(text: str, *, fallback: str = "role") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:48] or fallback)


def _ensure_item_id(item: dict[str, Any], *, prefix: str) -> dict[str, Any]:
    out = dict(item)
    if not out.get("id"):
        out["id"] = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return out


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_NOISE_TITLE_LINES = {
    "skip to main content",
    "search jobs",
    "home",
    "about us",
    "apply for this role",
    "view full job description",
    "candidate login",
    "sign in",
}

_WORKDAY_EMPTY_TAIL = re.compile(
    r"(?:\n|^)\s*(?:"
    r"Responsibilities if Required|"
    r"Education if Required|"
    r"License/?Registration/?Certification\s*Requirements|"
    r"Requirements"
    r")\s*:?\s*$",
    re.I | re.M,
)


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", text)
    # Preserve readable structure before nuking tags.
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|li|tr|h[1-6]|section|article)>", "\n", text)
    text = re.sub(r"(?is)<(p|div|h[1-6]|section|article|tr)(\s[^>]*)?>", "\n", text)
    text = re.sub(r"(?is)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_jd_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Drop trailing empty Workday template headers that look like clipped content.
    prev = None
    while prev != text:
        prev = text
        text = _WORKDAY_EMPTY_TAIL.sub("", text).rstrip(" \n\t:")
    return text.strip()[:24000]


def _meta_content(raw: str, *keys: str) -> str:
    for key in keys:
        patterns = [
            rf'(?is)<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(key)}["\']',
            rf'(?is)<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(key)}["\']',
        ]
        for pat in patterns:
            m = re.search(pat, raw)
            if m:
                return html.unescape(m.group(1)).strip()
    return ""


def _extract_html_jd(raw: str) -> tuple[str, dict[str, str]]:
    """Prefer job-description / hero blocks over whole-page chrome."""
    fields: dict[str, str] = {"company": "", "title": "", "location": ""}
    title_tag = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    if title_tag:
        title_full = html.unescape(re.sub(r"\s+", " ", title_tag.group(1))).strip()
        parts = [p.strip() for p in title_full.split("|") if p.strip()]
        if parts:
            fields["title"] = parts[0][:120]
        if len(parts) >= 2:
            fields["location"] = parts[1][:120]
        if len(parts) >= 3:
            fields["company"] = parts[2][:120]

    og_title = _meta_content(raw, "og:title")
    if og_title:
        m = re.match(r"^(.*?)\s+at\s+(.+)$", og_title, re.I)
        if m:
            fields["title"] = fields["title"] or m.group(1).strip()[:120]
            fields["company"] = fields["company"] or m.group(2).strip()[:120]
        elif not fields["title"]:
            fields["title"] = og_title[:120]

    hero = re.search(
        r'(?is)<h1[^>]*class=["\'][^"\']*hero-title[^"\']*["\'][^>]*>\s*(?:<span>)?(.*?)(?:</span>)?\s*</h1>',
        raw,
    )
    if hero:
        fields["title"] = (
            html.unescape(re.sub(r"<[^>]+>", "", hero.group(1))).strip()[:120] or fields["title"]
        )

    loc = re.search(
        r'(?is)class=["\'][^"\']*meta-location[^"\']*["\'][^>]*>.*?<span>(.*?)</span>',
        raw,
    )
    if loc:
        fields["location"] = (
            html.unescape(re.sub(r"<[^>]+>", "", loc.group(1))).strip()[:120] or fields["location"]
        )

    chunks: list[str] = []
    for pat in (
        r'(?is)<div[^>]+id=["\']job-description["\'][^>]*>(.*?)</div>\s*</div>',
        r'(?is)<section[^>]+id=["\']job-description["\'][^>]*>(.*?)</section>',
        r'(?is)<div[^>]+class=["\'][^"\']*job-description[^"\']*["\'][^>]*>(.*?)</div>',
    ):
        m = re.search(pat, raw)
        if m and len(m.group(1)) > 400:
            chunks.append(_strip_html(m.group(1)))
            break
    body = chunks[0] if chunks else _strip_html(raw)
    # Drop extreme nav chrome if we fell back to full page.
    if not chunks and len(body) > 2500:
        idx = body.lower().find("job description")
        if idx > 0:
            body = body[idx:]
    return _clean_jd_text(body), fields


def _workday_cxs_url(url: str) -> str | None:
    """Map Workday job/apply URLs to the public CXS JSON endpoint."""
    m = re.match(
        r"(?i)^https?://([a-z0-9-]+)\.wd(\d+)\.myworkdayjobs\.com/"
        r"(?:en-US/)?"
        r"([^/]+)/job/(.+?)(?:/apply)?/?$",
        (url or "").strip(),
    )
    if not m:
        return None
    tenant, wd_n, site, path = m.group(1), m.group(2), m.group(3), m.group(4)
    path = path.split("?")[0].rstrip("/")
    return f"https://{tenant}.wd{wd_n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{path}"


def _find_workday_job_url(html_text: str) -> str | None:
    for u in re.findall(
        r"https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[^\"'\s>]+/job/[^\"'\s>]+",
        html_text,
        flags=re.I,
    ):
        u = html.unescape(u).split("?")[0].rstrip("/")
        if u.lower().endswith("/apply"):
            u = u[: -len("/apply")]
        return u
    return None


def _extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    parts = [node.text for node in root.findall(".//w:t", ns) if node.text]
    return "\n".join(parts).strip()


def _extract_pdf_text(data: bytes) -> str:
    # Minimal best-effort PDF text scrape without extra dependencies.
    out: list[str] = []
    for m in re.finditer(rb"\((?:\\.|[^\\)]){1,200}\)", data):
        chunk = m.group(0)[1:-1]
        s = chunk.decode("latin-1", errors="ignore")
        s = s.replace("\\n", "\n").replace("\\r", "").replace("\\t", " ")
        s = re.sub(r"\\[0-9]{1,3}", "", s)
        if sum(c.isalnum() for c in s) >= 4:
            out.append(s)
    text = re.sub(r"\s+", " ", " ".join(out)).strip()
    return text[:20000]


def _guess_fields(jd_text: str, *, url: str = "", hints: dict[str, str] | None = None) -> dict[str, str]:
    hints = hints or {}
    company = hints.get("company") or ""
    title = hints.get("title") or ""
    location = hints.get("location") or ""
    lines = [ln.strip() for ln in jd_text.splitlines() if ln.strip()]
    for ln in lines[:40]:
        m = re.search(r"(?i)^(?:job\s*title|title|position)\s*[:\-]\s*(.+)$", ln)
        if m and not title:
            title = m.group(1).strip()[:120]
        m = re.search(r"(?i)^(?:company|employer|organization)\s*[:\-]\s*(.+)$", ln)
        if m and not company:
            company = m.group(1).strip()[:120]
        m = re.search(r"(?i)^(?:location|based in)\s*[:\-]\s*(.+)$", ln)
        if m and not location:
            location = m.group(1).strip()[:120]
    if not title:
        for ln in lines[:12]:
            low = ln.lower()
            if low in _NOISE_TITLE_LINES or low.startswith("http"):
                continue
            if 3 <= len(ln) <= 120:
                title = ln[:120]
                break
    if not company:
        for ln in lines[:8]:
            if re.search(r"\b(inc|llc|corp|company|labs|cloud)\b", ln, re.I) and len(ln) < 80:
                company = ln
                break
    if not company and url:
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        base = host.split(".")[0]
        if base.lower() not in {"careers", "jobs", "www", "apply"}:
            company = base.replace("-", " ").title()
        elif len(host.split(".")) >= 2:
            company = host.split(".")[-2].replace("-", " ").title()
    if not location:
        for ln in lines[:12]:
            if re.search(r"\b(remote|hybrid|on-?site|,\s*[A-Z]{2})\b", ln, re.I):
                location = ln[:120]
                break
    return {"title": title, "company": company, "location": location}


class CareerService:
    def __init__(
        self, store: SqlAlchemyStorage, llm: LlmAdapter, *, ingest_url_enabled: bool = True
    ) -> None:
        self.store = store
        self.llm = llm
        self.ingest_url_enabled = ingest_url_enabled

    def demo_seed(self, owner: str) -> str | None:
        """Seed a fresh demo vault for ``owner`` if it has no truth yet. Returns the sample app id."""
        from sprucer.demo import seed_demo_vault

        existing = self.store.get_truth(owner)
        if existing.get("profile"):
            return None
        return seed_demo_vault(self.store, owner)

    def demo_cleanup(self, *, ttl_hours: int, prefix: str = "demo-") -> int:
        """Sweep stale per-session demo vaults older than ``ttl_hours``."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max(1, ttl_hours))
        return self.store.purge_owner_prefix(prefix, cutoff)

    def truth_get(self, *, owner: str = SHARED_OWNER) -> dict[str, Any]:
        truth = self.store.get_truth(owner)
        counts = {
            "experience": len(truth.get("experience") or []),
            "projects": len(truth.get("projects") or []),
            "metrics": len(truth.get("metrics") or []),
            "blurbs": len(truth.get("blurbs") or []),
            "signatureStories": len(truth.get("signatureStories") or []),
            "neverClaim": len(truth.get("neverClaim") or []),
        }
        return {"ok": True, "truth": truth, "counts": counts}

    def truth_patch(
        self,
        *,
        op: str,
        section: str,
        item: dict[str, Any] | None = None,
        item_id: str | None = None,
        index: int | None = None,
        value: Any = None,
        confirm: bool = False,
        owner: str = SHARED_OWNER,
    ) -> dict[str, Any]:
        op = (op or "").strip().lower()
        truth = self.store.get_truth(owner)
        if op == "set":
            truth[section] = value
        elif op == "add":
            if section not in LIST_SECTIONS:
                raise RuntimeError(f"section must be one of {sorted(LIST_SECTIONS)}")
            if section == "neverClaim":
                text = ""
                if isinstance(item, dict):
                    text = str(item.get("text") or item.get("body") or "")
                elif isinstance(item, str):
                    text = item
                if not text.strip():
                    raise RuntimeError("neverClaim add requires text")
                lst = list(truth.get(section) or [])
                lst.append(text.strip())
                truth[section] = lst
            else:
                if not isinstance(item, dict):
                    raise RuntimeError("item object required")
                prefix = {
                    "signatureStories": "story",
                    "blurbs": "blurb",
                    "metrics": "metric",
                    "experience": "exp",
                    "projects": "proj",
                }[section]
                lst = list(truth.get(section) or [])
                lst.append(_ensure_item_id(item, prefix=prefix))
                truth[section] = lst
        elif op == "update":
            if section not in LIST_SECTIONS or section == "neverClaim":
                raise RuntimeError("update supports object list sections only")
            if not isinstance(item, dict):
                raise RuntimeError("item object required")
            lst = list(truth.get(section) or [])
            target = None
            if item_id:
                for i, existing in enumerate(lst):
                    if isinstance(existing, dict) and existing.get("id") == item_id:
                        target = i
                        break
            elif index is not None:
                target = index
            if target is None or target < 0 or target >= len(lst):
                raise RuntimeError("item not found")
            merged = dict(lst[target])
            merged.update(item)
            lst[target] = merged
            truth[section] = lst
        elif op == "remove":
            if not confirm:
                raise RuntimeError("confirm=true is required to remove")
            if section == "neverClaim":
                lst = list(truth.get(section) or [])
                if index is None or index < 0 or index >= len(lst):
                    raise RuntimeError("index required for neverClaim remove")
                lst.pop(index)
                truth[section] = lst
            else:
                lst = list(truth.get(section) or [])
                if item_id:
                    lst = [x for x in lst if not (isinstance(x, dict) and x.get("id") == item_id)]
                elif index is not None:
                    lst.pop(index)
                else:
                    raise RuntimeError("item_id or index required")
                truth[section] = lst
        else:
            raise RuntimeError("op must be add|update|remove|set")
        saved = self.store.save_truth(truth, owner)
        return {"ok": True, "truth": saved}

    def applications_list(self, *, owner: str = SHARED_OWNER) -> dict[str, Any]:
        return {"ok": True, "items": self.store.list_applications(owner)}

    def applications_get(self, application_id: str, *, owner: str = SHARED_OWNER) -> dict[str, Any]:
        app = self.store.get_application(application_id, owner)
        if app is None:
            raise RuntimeError(f"Application not found: {application_id}")
        return {"ok": True, "application": app}

    def applications_upsert(self, body: dict[str, Any], *, owner: str = SHARED_OWNER) -> dict[str, Any]:
        app_id = (body.get("id") or body.get("application_id") or "").strip()
        if not app_id:
            raise RuntimeError("id is required")
        existing = self.store.get_application(app_id, owner) or {"id": app_id, "generations": []}
        for key in ("company", "title", "location", "url", "status", "notes"):
            if key in body and body[key] is not None:
                existing[key] = body[key]
        try:
            saved = self.store.upsert_application(existing, owner=owner)
        except KeyError as exc:
            raise RuntimeError(f"Application id conflicts with another vault: {app_id}") from exc
        return {"ok": True, "application": saved}

    def applications_delete(
        self, application_id: str, *, confirm: bool = False, owner: str = SHARED_OWNER
    ) -> dict[str, Any]:
        if not confirm:
            raise RuntimeError("confirm=true is required to delete an application")
        try:
            self.store.delete_application(application_id, owner)
        except KeyError as exc:
            raise RuntimeError(f"Application not found: {application_id}") from exc
        return {"ok": True, "deleted": application_id}

    async def jd_ingest(
        self,
        *,
        source_type: str,
        text: str | None = None,
        url: str | None = None,
        filename: str | None = None,
        content_base64: str | None = None,
        application_id: str | None = None,
        owner: str = SHARED_OWNER,
    ) -> dict[str, Any]:
        import base64

        source_type = (source_type or "").strip().lower()
        if source_type not in {"paste", "url", "upload"}:
            raise RuntimeError("source_type must be paste|url|upload")

        jd_text = ""
        source_filename = filename or ""
        hints: dict[str, str] = {}

        if source_type == "paste":
            jd_text = (text or "").strip()
            if not jd_text:
                raise RuntimeError("text is required for paste")
        elif source_type == "url":
            if not self.ingest_url_enabled:
                raise RuntimeError(
                    "URL fetch is disabled in this deployment. Paste the JD text or upload a file instead."
                )
            if not (url or "").strip():
                raise RuntimeError("url is required for url ingest")
            jd_text, hints = await self._fetch_url_text(url.strip())
        else:
            if not content_base64:
                raise RuntimeError("content_base64 is required for upload")
            raw = base64.b64decode(content_base64)
            source_filename = filename or "upload.bin"
            lower = source_filename.lower()
            if lower.endswith(".docx"):
                jd_text = _extract_docx_text(raw)
            elif lower.endswith(".pdf"):
                jd_text = _extract_pdf_text(raw)
            elif lower.endswith((".html", ".htm")):
                jd_text = _strip_html(raw.decode("utf-8", errors="replace"))
            elif lower.endswith(".doc"):
                # Binary .doc: best-effort text scrape of printable ASCII.
                doc = raw.decode("latin-1", errors="ignore")
                doc = re.sub(r"[^\x09\x0a\x0d\x20-\x7e]+", " ", doc)
                jd_text = re.sub(r"\s+", " ", doc).strip()[:20000]
            else:
                jd_text = raw.decode("utf-8", errors="replace")
            if not jd_text.strip():
                jd_text = f"[binary upload stored as {source_filename}; text extraction empty]"

        jd_text = _clean_jd_text(jd_text)
        fields = _guess_fields(jd_text, url=url or "", hints=hints)
        day = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
        okey = owner_key(owner)

        if application_id and application_id.strip():
            app_id = application_id.strip()
        else:
            app_id = ""
            want_url = (url or "").strip().split("#")[0]
            if want_url:
                for prev in self.store.list_applications(owner):
                    if str(prev.get("url") or "").split("#")[0] == want_url:
                        app_id = str(prev["id"])
                        break
            if not app_id:
                stem = f"{day}-{_slug(fields['company'] or 'company')}-{_slug(fields['title'] or 'role')}"
                app_id = f"{okey}-{stem}" if okey != "shared" else stem
                base = app_id
                n = 2
                while self.store.get_application(app_id, owner) is not None:
                    app_id = f"{base}-{n}"
                    n += 1

        existing = self.store.get_application(app_id, owner)
        sha = hashlib.sha256(jd_text.encode("utf-8", errors="replace")).hexdigest()
        meta = {
            "id": app_id,
            "company": fields["company"],
            "title": fields["title"],
            "location": fields["location"],
            "url": (url or "").strip(),
            "sourceType": source_type,
            "sourceFilename": source_filename,
            "sourceSha256": sha,
            "status": (existing or {}).get("status") or "draft",
            "notes": (existing or {}).get("notes") or "",
        }
        if existing:
            for key in ("company", "title", "location", "url"):
                if existing.get(key) and not meta.get(key):
                    meta[key] = existing[key]
        try:
            saved = self.store.upsert_application(meta, jd_text=jd_text, owner=owner)
        except KeyError as exc:
            raise RuntimeError(f"Application id conflicts with another vault: {app_id}") from exc
        return {"ok": True, "application": saved, "jdChars": len(jd_text)}

    async def _safe_get(
        self, client: httpx.AsyncClient, url: str, headers: dict[str, str]
    ) -> httpx.Response:
        """GET ``url`` following redirects manually, re-checking SSRF on every hop."""
        from sprucer.ssrf import UnsafeUrlError, assert_public_http_url

        try:
            current = assert_public_http_url(url)
        except UnsafeUrlError as exc:
            raise RuntimeError(str(exc)) from exc
        for _ in range(6):
            try:
                res = await client.get(current, headers=headers)
            except httpx.RequestError as exc:
                raise RuntimeError(f"URL fetch failed: {exc}") from exc
            if res.is_redirect:
                loc = res.headers.get("location")
                if not loc:
                    raise RuntimeError("URL fetch redirect missing Location")
                nxt = urljoin(str(res.url), loc)
                try:
                    current = assert_public_http_url(nxt)
                except UnsafeUrlError as exc:
                    raise RuntimeError(str(exc)) from exc
                continue
            return res
        raise RuntimeError("URL fetch exceeded redirect limit")

    async def _fetch_workday_cxs(
        self, client: httpx.AsyncClient, job_url: str
    ) -> tuple[str, dict[str, str]] | None:
        """Fetch a Workday posting via its public CXS JSON endpoint (richer than the HTML shell)."""
        cxs = _workday_cxs_url(job_url)
        if not cxs:
            return None
        try:
            res = await self._safe_get(
                client, cxs, {"User-Agent": _BROWSER_UA, "Accept": "application/json"}
            )
        except RuntimeError:
            return None
        if res.status_code >= 400:
            return None
        try:
            data = res.json()
        except Exception:
            return None
        info = data.get("jobPostingInfo") or {}
        title = str(info.get("title") or "").strip()
        desc_html = str(info.get("jobDescription") or "")
        location = str(info.get("location") or info.get("additionalLocations") or "").strip()
        if isinstance(info.get("jobPostingLocation"), dict):
            location = location or str(info["jobPostingLocation"].get("descriptor") or "").strip()
        company = str(
            info.get("hiringOrganization")
            or (data.get("hiringOrganization") or {}).get("name")
            or ""
        ).strip()
        body = _strip_html(desc_html) if desc_html else ""
        if not body and not title:
            return None
        parts = [p for p in [title, location, body] if p]
        return _clean_jd_text("\n\n".join(parts)), {
            "company": company[:120],
            "title": title[:120],
            "location": location[:120],
        }

    async def _fetch_url_text(self, fetch_url: str) -> tuple[str, dict[str, str]]:
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        # Manual redirect following so each hop is re-checked (SSRF).
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False, trust_env=False) as client:
            # Direct Workday job URL -> CXS JSON (richer than the HTML apply shell).
            if "myworkdayjobs.com" in fetch_url.lower():
                wd = await self._fetch_workday_cxs(client, fetch_url)
                if wd:
                    return wd

            res = await self._safe_get(client, fetch_url, headers)
            if res.status_code in {401, 403}:
                raise RuntimeError(
                    f"Job site returned HTTP {res.status_code}. Paste the JD text or upload a file instead."
                )
            if res.status_code >= 400:
                raise RuntimeError(f"URL fetch HTTP {res.status_code}")

            ctype = (res.headers.get("content-type") or "").lower()
            if "pdf" in ctype or fetch_url.lower().split("?")[0].endswith(".pdf"):
                return _clean_jd_text(_extract_pdf_text(res.content)), {}

            body = res.text
            if "json" in ctype:
                try:
                    data = res.json()
                except Exception:
                    data = None
                if isinstance(data, dict) and data.get("jobPostingInfo"):
                    info = data["jobPostingInfo"]
                    desc = _strip_html(str(info.get("jobDescription") or ""))
                    title = str(info.get("title") or "")
                    text = _clean_jd_text("\n\n".join(p for p in [title, desc] if p))
                    return text, {"title": title[:120]}
                return _clean_jd_text(body), {}

            if "html" in ctype or "<html" in body[:500].lower():
                # Prefer an embedded Workday apply link -> CXS when present.
                wd_job = _find_workday_job_url(body)
                if wd_job:
                    wd = await self._fetch_workday_cxs(client, wd_job)
                    if wd and len(wd[0]) > 400:
                        return wd
                return _extract_html_jd(body)

            return _clean_jd_text(body), {}

    def _normalize_types(self, types: list[str] | None, custom_type: str | None) -> list[str]:
        out: list[str] = []
        for t in types or []:
            t = (t or "").strip().lower()
            if not t:
                continue
            if t in ARTIFACT_TYPES or t.startswith("custom:"):
                out.append(t)
            else:
                out.append(f"custom:{t}")
        if custom_type and custom_type.strip():
            key = f"custom:{_slug(custom_type.strip(), fallback='request')}"
            if key not in out:
                out.append(key)
        if not out:
            raise RuntimeError(
                f"Provide at least one artifact type (one of {ARTIFACT_TYPES}) or custom_type"
            )
        seen: set[str] = set()
        uniq: list[str] = []
        for t in out:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return uniq

    def _heading_instruction(self, artifact_types: list[str]) -> str:
        lines: list[str] = []
        for t in artifact_types:
            if t == "cover":
                lines.append("- ## Cover Letter  (full letter body only)")
            elif t == "email":
                lines.append(
                    "- ## Email  (must include Subject: line, greeting, short body, sign-off — NOT a cover letter)"
                )
            elif t == "resume":
                lines.append("- ## Resume")
            elif t == "interview":
                lines.append(
                    "- ## Interview Prep  (practice questions + knowledge-bank refresh notes — see format guide)"
                )
            elif t == "linkedin":
                lines.append(
                    "- ## LinkedIn DM  (short blind-reach chat message to a hiring manager/recruiter — NOT a resume or profile rewrite)"
                )
            else:
                label = t.replace("custom:", "").replace("-", " ").title()
                lines.append(f"- ## {label}")
        forbidden = []
        if "email" in artifact_types and "cover" not in artifact_types:
            forbidden.append("Do NOT write ## Cover Letter or any cover-letter prose.")
        if "cover" in artifact_types and "email" not in artifact_types:
            forbidden.append("Do NOT write ## Email.")
        if "linkedin" in artifact_types:
            forbidden.append(
                "For LinkedIn DM: do NOT write profile-optimization advice, resume bullets, "
                "or 'update your LinkedIn' homework. Write the actual message text to send."
            )
        if "interview" in artifact_types:
            forbidden.append(
                "For Interview Prep: do NOT write only vague study tips. Include concrete practice "
                "questions with Answer with: coaching lines that cite real vault ids. "
                "Do NOT invent Project/Story/Metric numeric IDs."
            )
        return "\n".join(lines + forbidden)

    def _interview_cite_catalog(self, truth: dict[str, Any]) -> list[dict[str, str]]:
        """Compact, real vault pointers the model must cite — never invent ticket-style IDs."""
        items: list[dict[str, str]] = []

        def add(kind: str, item_id: str, label: str) -> None:
            label = re.sub(r"\s+", " ", (label or "").strip())
            if not label:
                return
            items.append({"kind": kind, "id": (item_id or "").strip(), "label": label[:180]})

        for m in truth.get("metrics") or []:
            if isinstance(m, dict):
                add("metric", str(m.get("id") or ""), str(m.get("text") or ""))
        for p in truth.get("projects") or []:
            if isinstance(p, dict):
                add(
                    "project",
                    str(p.get("id") or ""),
                    str(p.get("title") or p.get("name") or p.get("text") or ""),
                )
        for e in truth.get("experience") or []:
            if isinstance(e, dict):
                role = str(e.get("role") or "").strip()
                org = str(e.get("employer") or e.get("company") or e.get("org") or "").strip()
                label = " @ ".join(x for x in (role, org) if x) or str(e.get("summary") or "")
                add("experience", str(e.get("id") or ""), label)
        for s in truth.get("signatureStories") or []:
            if isinstance(s, dict):
                add(
                    "story",
                    str(s.get("id") or ""),
                    str(s.get("title") or s.get("name") or s.get("text") or ""),
                )
        for b in truth.get("blurbs") or []:
            if isinstance(b, dict):
                add(
                    "blurb",
                    str(b.get("id") or ""),
                    str(b.get("title") or b.get("text") or b.get("body") or ""),
                )
        for nc in truth.get("neverClaim") or []:
            text = nc.get("text") if isinstance(nc, dict) else str(nc)
            add("neverClaim", "", str(text or ""))
        return items[:40]

    def _artifact_format_guides(
        self, artifact_types: list[str], *, cite_catalog: list[dict[str, str]] | None = None
    ) -> str:
        guides: list[str] = []
        if "interview" in artifact_types:
            catalog_lines = []
            for row in cite_catalog or []:
                cid = (row.get("id") or "").strip()
                label = (row.get("label") or "").strip()
                kind = (row.get("kind") or "").strip()
                if cid:
                    catalog_lines.append(f"- [{kind}] id=`{cid}` — {label}")
                else:
                    catalog_lines.append(f"- [{kind}] — {label}")
            catalog_block = (
                "\n".join(catalog_lines)
                if catalog_lines
                else "(careerTruth lists are thin — coach with transferable honesty; still invent nothing.)"
            )
            guides.append(
                "Interview Prep purpose: a usable cheat-sheet for THIS interview. "
                "Each coaching line must help the candidate speak from real vault facts — "
                "not invent ticket numbers or pretend Epic/HIPAA tenure they do not have.\n"
                "Interview Prep format (under ## Interview Prep):\n"
                "1) ### Likely questions — 6 to 10 realistic questions for THIS JD/role. Number them.\n"
                "   After each question, ONE coaching line in this shape:\n"
                "   Answer with: <one concrete sentence of how to answer, grounded in a real catalog item>. "
                "Cite: `<exact id>` (copy an id from interviewCiteCatalog when one fits).\n"
                "   Bridge transferable experience honestly when the JD asks for tools you have not used "
                "(e.g. map hosting/ops/SQL work to application-analyst themes). Never invent employers, "
                "Epic modules, certifications, or metrics.\n"
                "   Forbidden: fake numeric IDs like 'Project ID 00345', 'Story ID 12345', 'Metric ID 00789'. "
                "Forbidden: coaching that only says 'Use/Refer to ID …' with no answer substance.\n"
                "2) ### Refresh from your knowledge bank — bullets of real interviewCiteCatalog entries "
                "worth re-reading before the interview (use `id` — label). Do not invent new entries.\n"
                "3) ### Watch-outs — 2 to 4 items quoted/paraphrased from neverClaim that matter here.\n"
                "Tone: coach for live answers.\n"
                f"interviewCiteCatalog (cite ONLY from this list):\n{catalog_block}"
            )
        if "linkedin" in artifact_types:
            guides.append(
                "LinkedIn DM format (under ## LinkedIn DM):\n"
                "Write the exact short message someone would paste into LinkedIn, Indeed, or similar "
                "chat/DM — a blind reach to a hiring manager, recruiter, or hiring-team contact.\n"
                "Constraints: 4 to 8 short sentences max (chat length). First person. Warm and direct. "
                "One concrete hook from careerTruth tied to the JD. One clear ask (quick chat, referral, "
                "or how to apply). No Subject line. No resume sections. No bullet dump of experience. "
                "No advice about improving a LinkedIn profile. Optional first line: Hi {Name}/Hi team —"
            )
        if "email" in artifact_types:
            guides.append(
                "Email format: Subject: line, then greeting, short body, sign-off. Not a cover letter."
            )
        if "cover" in artifact_types:
            guides.append("Cover Letter format: full letter body ready to send.")
        if "resume" in artifact_types:
            guides.append(
                "Resume format: tailored resume sections/bullets for this JD, grounded in careerTruth."
            )
        return "\n\n".join(guides)

    def _content_satisfies_types(
        self,
        content: str,
        artifact_types: list[str],
        *,
        cite_ids: set[str] | None = None,
    ) -> bool:
        headings = [
            re.sub(r"^#+\s*", "", ln).strip().lower()
            for ln in content.splitlines()
            if re.match(r"^#{1,3}\s+\S", ln)
        ]
        low = content.lower()
        for t in artifact_types:
            if t.startswith("custom:"):
                label = t.split(":", 1)[1].replace("-", " ")
                if not any(label in h for h in headings):
                    return False
                continue
            aliases = ARTIFACT_HEADINGS.get(t, (t,))
            if not any(any(alias == h or h.startswith(alias) for alias in aliases) for h in headings):
                return False
        # Email-only requests must not be a cover letter in disguise
        if artifact_types == ["email"] or (
            "email" in artifact_types and "cover" not in artifact_types
        ):
            if any(h == "cover letter" or h.startswith("cover letter") for h in headings):
                if artifact_types == ["email"]:
                    return False
            if artifact_types == ["email"] and "subject:" not in low:
                return False
        if "linkedin" in artifact_types:
            profile_advice_markers = (
                "enhance my profile",
                "enhance your profile",
                "update your linkedin",
                "professional experience :",
                "professional experience:",
                "recommend focusing on the following elements",
                "showcase your relevant skills",
            )
            if any(m in low for m in profile_advice_markers):
                return False
            # Must look like a sendable message, not a resume outline
            if "linkedin" in artifact_types and "resume" not in artifact_types:
                if low.count("\n- ") + low.count("\n* ") > 8 and "hi " not in low[:400]:
                    return False
            if "[your name]" in low or "your name]" in low:
                return False
        if "interview" in artifact_types:
            # Require actual practice-question energy, not only study tips
            question_marks = content.count("?")
            has_questions_heading = any("question" in h for h in headings)
            if question_marks < 3 and not has_questions_heading:
                return False
            # Reject invented ticket-style IDs (the failure mode we saw from small models)
            if re.search(
                r"\b(?:project|story|metric|blurb|experience)\s+id\s+\d{3,}\b",
                content,
                flags=re.I,
            ):
                return False
            real_ids = {x for x in (cite_ids or set()) if x}
            if real_ids:
                hits = sum(1 for cid in real_ids if cid in content)
                if hits < 2:
                    return False
            # Coaching must not be ID-pointer-only; require answer-hook language somewhere
            if not re.search(r"answer with\s*:", content, flags=re.I):
                # Allow softer phrasing if vault ids are clearly cited
                if not real_ids or sum(1 for cid in real_ids if cid in content) < 3:
                    return False
        return True

    def _emphasis_hint(self, truth: dict[str, Any], jd_text: str) -> list[dict[str, str]]:
        blob = jd_text.lower()
        plan: list[dict[str, str]] = []
        for m in truth.get("metrics") or []:
            if not isinstance(m, dict):
                continue
            tags = " ".join(m.get("tags") or []).lower()
            text = (m.get("text") or "").lower()
            score = sum(1 for w in re.findall(r"[a-z0-9]{4,}", tags + " " + text) if w in blob)
            if score:
                plan.append(
                    {
                        "id": m.get("id") or "",
                        "reason": f"metric overlap score {score}",
                        "text": m.get("text") or "",
                    }
                )
        for blurb in truth.get("blurbs") or []:
            if not isinstance(blurb, dict):
                continue
            hay = " ".join(
                [
                    " ".join(blurb.get("tags") or []),
                    blurb.get("text") or "",
                    blurb.get("label") or "",
                ]
            ).lower()
            score = sum(1 for w in re.findall(r"[a-z0-9]{4,}", hay) if w in blob)
            if score:
                plan.append(
                    {
                        "id": blurb.get("id") or "",
                        "reason": f"blurb overlap score {score}",
                        "text": blurb.get("text") or "",
                    }
                )
        plan.insert(
            0,
            {
                "id": "roleSpectrum",
                "reason": "operator prefers spectrum fit over title match",
                "text": (truth.get("roleSpectrum") or {}).get("summary") or "",
            },
        )
        return plan[:16]

    def _type_batches(self, artifact_types: list[str]) -> list[list[str]]:
        """Split multi-type generates so small local models are not asked for 5 full drafts at once."""
        if len(artifact_types) <= 2:
            return [list(artifact_types)]
        weights = {
            "interview": 3,
            "resume": 2,
            "cover": 2,
            "email": 1,
            "linkedin": 1,
        }
        batches: list[list[str]] = []
        current: list[str] = []
        budget = 0
        limit = 3
        for t in artifact_types:
            w = 2 if t.startswith("custom:") else weights.get(t, 2)
            # Interview is format-heavy — always its own call when bundling many types.
            if t == "interview" and current:
                batches.append(current)
                current = []
                budget = 0
            if current and budget + w > limit:
                batches.append(current)
                current = []
                budget = 0
            current.append(t)
            budget += w
            if t == "interview" or budget >= limit:
                batches.append(current)
                current = []
                budget = 0
        if current:
            batches.append(current)
        return batches

    def _max_tokens_for(self, artifact_types: list[str]) -> int:
        base = 1000 * max(1, len(artifact_types))
        if "interview" in artifact_types:
            base += 1000
        if "resume" in artifact_types:
            base += 400
        return min(4500, max(2000, base))

    async def _generate_batch_markdown(
        self,
        *,
        app: dict[str, Any],
        truth: dict[str, Any],
        jd_text: str,
        artifact_types: list[str],
        emphasis: list[dict[str, str]],
        llm: LlmAdapter | None = None,
    ) -> str:
        llm = llm or self.llm
        cite_catalog = (
            self._interview_cite_catalog(truth) if "interview" in artifact_types else []
        )
        cite_ids = {row["id"] for row in cite_catalog if row.get("id")}
        heading_block = self._heading_instruction(artifact_types)
        format_guides = self._artifact_format_guides(
            artifact_types, cite_catalog=cite_catalog or None
        )
        system = (
            "You write tailored job-application materials grounded ONLY in the provided "
            "career truth JSON and job description. Never invent employers, dates, degrees, "
            "or metrics. Never use em dashes or fancy punctuation; keyboard characters only. "
            "Respect neverClaim. Choose strengths and superlatives dynamically for this JD. "
            "Return Markdown with EXACTLY these ## sections (and no others):\n"
            f"{heading_block}\n\n"
            "Format guides for the requested artifacts:\n"
            f"{format_guides}\n\n"
            "Write only the deliverable under those headings (sendable copy, or prep the candidate can use). "
            "Do not include an Emphasis section, emphasis plan, or other meta commentary."
        )
        user: dict[str, Any] = {
            "application": {
                "id": app.get("id"),
                "company": app.get("company"),
                "title": app.get("title"),
                "location": app.get("location"),
                "url": app.get("url"),
            },
            "artifactTypes": artifact_types,
            "requiredHeadings": heading_block,
            "formatGuides": format_guides,
            "emphasisPlanHint": emphasis,
            "careerTruth": truth,
            "jobDescription": jd_text[:24000],
        }
        if cite_catalog:
            user["interviewCiteCatalog"] = cite_catalog
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=True)},
        ]
        max_tokens = self._max_tokens_for(artifact_types)
        content = await llm.chat(messages, max_tokens=max_tokens)
        if not self._content_satisfies_types(content, artifact_types, cite_ids=cite_ids):
            repair = (
                "Your previous reply did not match the required artifact format. "
                f"Rewrite the entire response. Required ## sections only:\n{heading_block}\n\n"
                f"Follow these format guides exactly:\n{format_guides}\n"
                "Do not include any other ## headings. Keep each section concise enough to finish."
            )
            if "interview" in artifact_types:
                repair += (
                    "\nFor Interview Prep: use Answer with: … Cite: `real-id` lines. "
                    "Cite only ids from interviewCiteCatalog. Never invent Project/Story/Metric "
                    "numeric ticket IDs. Do not claim Epic or HIPAA work absent from careerTruth."
                )
            if "linkedin" in artifact_types:
                profile = truth.get("profile") if isinstance(truth.get("profile"), dict) else {}
                sign = (profile or {}).get("shortName") or (profile or {}).get("name") or ""
                repair += (
                    "\nFor LinkedIn DM: write the sendable message only; sign with the real name "
                    f"from careerTruth.profile ({sign or 'profile.name'}), never [Your Name]."
                )
            content = await llm.chat(
                messages
                + [
                    {"role": "assistant", "content": content.strip()[:4000]},
                    {"role": "user", "content": repair},
                ],
                max_tokens=max_tokens,
            )
            if not self._content_satisfies_types(content, artifact_types, cite_ids=cite_ids):
                raise RuntimeError(
                    "Model returned the wrong artifact type or unusable interview prep "
                    f"for ({', '.join(artifact_types)}). Try fewer types at once, generate again, "
                    "or use a stronger LLM."
                )
        return content.strip()

    async def generate(
        self,
        *,
        application_id: str,
        types: list[str] | None = None,
        custom_type: str | None = None,
        owner: str = SHARED_OWNER,
        llm_override: LlmAdapter | None = None,
    ) -> dict[str, Any]:
        app = self.store.get_application(application_id, owner)
        if app is None:
            raise RuntimeError(f"Application not found: {application_id}")
        jd_text = self.store.get_jd(application_id, owner)
        if not jd_text.strip():
            raise RuntimeError("JD missing; ingest a job description first")
        truth = self.store.get_truth(owner)
        artifact_types = self._normalize_types(types, custom_type)
        emphasis = self._emphasis_hint(truth, jd_text)
        batches = self._type_batches(artifact_types)

        started = time.perf_counter()
        parts: list[str] = []
        for batch in batches:
            part = await self._generate_batch_markdown(
                app=app,
                truth=truth,
                jd_text=jd_text,
                artifact_types=batch,
                emphasis=emphasis,
                llm=llm_override,
            )
            parts.append(part)
        content = "\n\n".join(parts)
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))

        gen_id = f"gen-{uuid.uuid4().hex[:10]}"
        # Emphasis plan stays in generation sidecar metadata — not in the user-facing body.
        company = (app.get("company") or "").strip()
        title = (app.get("title") or "").strip()
        location = (app.get("location") or "").strip()
        app_line = " — ".join(p for p in (company, location, title) if p) or "Application"
        body = (
            f"<!-- generation_id={gen_id} approval=draft types={','.join(artifact_types)} -->\n\n"
            f"# Generation {gen_id}\n\n"
            f"Application: {app_line}\n\n"
            + content.strip()
            + "\n"
        )
        sidecar = {
            "id": gen_id,
            "types": artifact_types,
            "emphasisPlan": emphasis,
            "approval": "draft",
            "durationMs": duration_ms,
        }
        saved = self.store.save_generation(application_id, generation=sidecar, content=body, owner=owner)
        return {
            "ok": True,
            "application_id": application_id,
            "generation": saved,
            "preview": body[:800],
        }

    def generation_approve(
        self,
        *,
        application_id: str,
        generation_id: str,
        confirm: bool = False,
        owner: str = SHARED_OWNER,
    ) -> dict[str, Any]:
        if not confirm:
            raise RuntimeError("confirm=true is required to approve a generation")
        try:
            found = self.store.approve_generation(application_id, generation_id, owner)
        except KeyError as exc:
            raise RuntimeError(f"generation not found: {generation_id}") from exc
        return {"ok": True, "generation": found, "application_id": application_id}

    def generation_delete(
        self,
        *,
        application_id: str,
        generation_id: str,
        confirm: bool = False,
        owner: str = SHARED_OWNER,
    ) -> dict[str, Any]:
        if not confirm:
            raise RuntimeError("confirm=true is required to delete a generation")
        try:
            self.store.delete_generation(application_id, generation_id, owner)
        except KeyError as exc:
            raise RuntimeError(f"generation not found: {generation_id}") from exc
        return {"ok": True, "deleted": generation_id, "application_id": application_id}

    def generation_get(
        self, *, application_id: str, generation_id: str, owner: str = SHARED_OWNER
    ) -> dict[str, Any]:
        found = self.store.get_generation(application_id, generation_id, owner)
        if found is None:
            raise RuntimeError(f"generation not found: {generation_id}")
        return {"ok": True, "generation": found, "content": found.get("content") or ""}

    # --- Career-fit interview (IDEA-16) ---

    def _fit_llm(self, llm_override: LlmAdapter | None) -> LlmAdapter:
        llm = llm_override or self.llm
        if isinstance(llm, DemoLlm):
            raise RuntimeError(
                "Career-fit interview needs a live LLM. "
                "Configure SPRUCER_LLM_* or bring your own AI (BYOK) — "
                "offline demo generator cannot run this flow."
            )
        return llm

    @staticmethod
    def _fit_vault_summary(truth: dict[str, Any]) -> dict[str, Any]:
        spectrum = truth.get("roleSpectrum") if isinstance(truth.get("roleSpectrum"), dict) else {}
        career_fit = truth.get("careerFit") if isinstance(truth.get("careerFit"), dict) else {}
        skills = truth.get("skills") if isinstance(truth.get("skills"), dict) else {}
        skill_groups = {
            k: [str(x) for x in (v or [])[:8]]
            for k, v in skills.items()
            if isinstance(v, list)
        }
        experience_roles: list[str] = []
        for exp in truth.get("experience") or []:
            if not isinstance(exp, dict):
                continue
            role = str(exp.get("role") or "").strip()
            org = str(exp.get("org") or "").strip()
            line = " at ".join(p for p in (role, org) if p)
            if line:
                experience_roles.append(line)
        return {
            "headline": str(truth.get("headline") or ""),
            "roleSpectrum": {
                "summary": str(spectrum.get("summary") or ""),
                "preferSenior": spectrum.get("preferSenior"),
                "titleNamesIrrelevant": spectrum.get("titleNamesIrrelevant"),
            },
            "careerFit": career_fit or {},
            "skills": skill_groups,
            "experienceRoles": experience_roles[:8],
            "neverClaim": [str(x) for x in (truth.get("neverClaim") or [])[:12]],
        }

    @staticmethod
    def _fit_has_existing_prefs(summary: dict[str, Any]) -> bool:
        career_fit = summary.get("careerFit") if isinstance(summary.get("careerFit"), dict) else {}
        industries = career_fit.get("industries") if isinstance(career_fit.get("industries"), list) else []
        titles = career_fit.get("titles") if isinstance(career_fit.get("titles"), list) else []
        if industries or titles:
            return True
        spectrum = summary.get("roleSpectrum") if isinstance(summary.get("roleSpectrum"), dict) else {}
        return bool(str(spectrum.get("summary") or "").strip())

    @staticmethod
    def _fit_opening_message(*, mode: str, summary: dict[str, Any]) -> str:
        if mode == "confirm":
            career_fit = summary.get("careerFit") if isinstance(summary.get("careerFit"), dict) else {}
            spectrum = summary.get("roleSpectrum") if isinstance(summary.get("roleSpectrum"), dict) else {}
            lines = [
                "I already see career-fit prefs in your knowledge bank. "
                "Please confirm, correct, or retire anything that is stale before I recommend industries and titles.",
                "",
            ]
            industries = career_fit.get("industries") if isinstance(career_fit.get("industries"), list) else []
            if industries:
                names = [
                    str(i.get("name") or i) if isinstance(i, dict) else str(i)
                    for i in industries
                    if isinstance(i, (dict, str))
                ]
                lines.append("Accepted industries: " + ", ".join(n for n in names if n) + ".")
            titles = career_fit.get("titles") if isinstance(career_fit.get("titles"), list) else []
            if titles:
                names = [
                    str(t.get("name") or t) if isinstance(t, dict) else str(t)
                    for t in titles
                    if isinstance(t, (dict, str))
                ]
                lines.append("Accepted titles / role families: " + ", ".join(n for n in names if n) + ".")
            spectrum_summary = str(spectrum.get("summary") or "").strip()
            if spectrum_summary:
                lines.append(f"Role spectrum summary: {spectrum_summary}")
            if not industries and not titles and spectrum_summary:
                lines.append(
                    "(No accepted careerFit block yet — only roleSpectrum. "
                    "Tell me what still holds and what to change.)"
                )
            lines.append("")
            lines.append(
                "Reply with confirmations and corrections. "
                "When you are ready for ranked industries and titles, say so."
            )
            return "\n".join(lines)
        return (
            "I do not see enough career-fit data yet, so I will inquire thoroughly.\n\n"
            "What kinds of problems, domains, or industries energize you at work — "
            "and what drains you? Include constraints (geo, remote, seniority, schedule) if you have them."
        )

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            raise RuntimeError("LLM returned an empty career-fit response")
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if fence:
            return json.loads(fence.group(1))
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise RuntimeError("LLM career-fit response was not valid JSON")

    @staticmethod
    def _normalize_ranked_items(raw: Any, *, require_name: bool = True) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for idx, item in enumerate(raw):
            if isinstance(item, str):
                name = item.strip()
                if not name:
                    continue
                out.append({"name": name, "rank": idx + 1, "rationale": ""})
                continue
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("title") or item.get("industry") or "").strip()
            if require_name and not name:
                continue
            entry: dict[str, Any] = {
                "name": name,
                "rank": int(item.get("rank") or idx + 1),
                "rationale": str(item.get("rationale") or item.get("reason") or "").strip(),
            }
            industry = str(item.get("industry") or "").strip()
            if industry:
                entry["industry"] = industry
            out.append(entry)
        out.sort(key=lambda r: int(r.get("rank") or 999))
        for i, row in enumerate(out):
            row["rank"] = i + 1
        return out

    def _normalize_fit_draft(self, data: dict[str, Any]) -> dict[str, Any]:
        industries = self._normalize_ranked_items(data.get("industries"))
        titles = self._normalize_ranked_items(data.get("titles"))
        if not industries:
            raise RuntimeError("career-fit draft must include at least one ranked industry")
        constraints_raw = data.get("constraints") if isinstance(data.get("constraints"), dict) else {}
        constraints = {
            "geo": str(constraints_raw.get("geo") or "").strip(),
            "remote": str(constraints_raw.get("remote") or "").strip(),
            "other": str(constraints_raw.get("other") or "").strip(),
        }
        confidence = str(data.get("confidence") or "medium").strip().lower()
        if confidence not in ("low", "medium", "high"):
            confidence = "medium"
        notes = str(data.get("notes") or "").strip()
        banned = ("http://", "https://", "job posting", "hiring at")
        for bucket in (industries, titles):
            for row in bucket:
                blob = f"{row.get('name')} {row.get('rationale')}".lower()
                if any(b in blob for b in banned):
                    raise RuntimeError(
                        "career-fit recommendations must not invent employers or job postings; "
                        "use industries and role families only"
                    )
        return {
            "industries": industries,
            "titles": titles,
            "constraints": constraints,
            "confidence": confidence,
            "notes": notes,
        }

    def fit_list(self, *, owner: str = SHARED_OWNER) -> dict[str, Any]:
        sessions = self.store.list_fit_sessions(owner)
        slim = [
            {
                "id": s.get("id"),
                "status": s.get("status"),
                "mode": s.get("mode"),
                "createdAt": s.get("createdAt"),
                "updatedAt": s.get("updatedAt"),
                "hasDraft": bool(s.get("draft")),
            }
            for s in sessions
        ]
        return {"ok": True, "sessions": slim}

    def fit_get(self, *, session_id: str, owner: str = SHARED_OWNER) -> dict[str, Any]:
        found = self.store.get_fit_session(session_id, owner)
        if found is None:
            raise RuntimeError(f"fit session not found: {session_id}")
        return {"ok": True, "session": found}

    def fit_start(self, *, owner: str = SHARED_OWNER) -> dict[str, Any]:
        truth = self.store.get_truth(owner)
        summary = self._fit_vault_summary(truth)
        mode = "confirm" if self._fit_has_existing_prefs(summary) else "inquire"
        opening = self._fit_opening_message(mode=mode, summary=summary)
        session_id = f"fit-{uuid.uuid4().hex[:12]}"
        payload = {
            "status": "interviewing",
            "mode": mode,
            "messages": [{"role": "assistant", "content": opening}],
            "vaultSummary": summary,
            "draft": None,
            "readyForRecommend": False,
        }
        saved = self.store.save_fit_session(session_id, payload, owner=owner)
        return {"ok": True, "session": saved}

    async def fit_turn(
        self,
        *,
        session_id: str,
        message: str,
        owner: str = SHARED_OWNER,
        llm_override: LlmAdapter | None = None,
    ) -> dict[str, Any]:
        text = (message or "").strip()
        if not text:
            raise RuntimeError("message is required")
        found = self.store.get_fit_session(session_id, owner)
        if found is None:
            raise RuntimeError(f"fit session not found: {session_id}")
        if found.get("status") == "accepted":
            raise RuntimeError("fit session already accepted; start a new interview to revise")
        llm = self._fit_llm(llm_override)
        messages = list(found.get("messages") or [])
        messages.append({"role": "user", "content": text})
        system = (
            "You are Sprucer's career-fit interviewer. Truth-first: ground follow-ups in the vault "
            "summary and prior answers. Never invent employers, job postings, salaries, or credentials. "
            "Keyboard punctuation only (no em dashes). Ask one focused question at a time unless the "
            "operator is clearly ready for recommendations. "
            "Respond with JSON only: "
            '{"assistantMessage":"...","readyForRecommend":false}'
        )
        user_payload = {
            "fitMode": "turn",
            "mode": found.get("mode"),
            "vaultSummary": found.get("vaultSummary") or {},
            "transcript": messages[-24:],
        }
        raw = await llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
            max_tokens=900,
            temperature=0.4,
        )
        parsed = self._parse_json_object(raw)
        assistant = str(parsed.get("assistantMessage") or parsed.get("message") or "").strip()
        if not assistant:
            raise RuntimeError("LLM did not return assistantMessage for career-fit turn")
        ready = bool(parsed.get("readyForRecommend"))
        messages.append({"role": "assistant", "content": assistant})
        found["messages"] = messages
        found["readyForRecommend"] = ready
        if found.get("status") == "draft":
            found["status"] = "interviewing"
        saved = self.store.save_fit_session(session_id, found, owner=owner)
        return {"ok": True, "session": saved}

    async def fit_recommend(
        self,
        *,
        session_id: str,
        owner: str = SHARED_OWNER,
        llm_override: LlmAdapter | None = None,
    ) -> dict[str, Any]:
        found = self.store.get_fit_session(session_id, owner)
        if found is None:
            raise RuntimeError(f"fit session not found: {session_id}")
        if found.get("status") == "accepted":
            raise RuntimeError("fit session already accepted; start a new interview to revise")
        messages = list(found.get("messages") or [])
        if len([m for m in messages if m.get("role") == "user"]) < 1:
            raise RuntimeError("answer at least one interview question before requesting recommendations")
        llm = self._fit_llm(llm_override)
        system = (
            "You are Sprucer's career-fit recommender. Rank industries (required) and job titles / "
            "role families (preferred) grounded ONLY in the interview transcript and vault summary. "
            "Never invent employers, job board postings, URLs, or salaries. Admit low confidence when "
            "evidence is thin. Keyboard punctuation only. "
            "Respond with JSON only: "
            '{"industries":[{"name":"...","rank":1,"rationale":"..."}],'
            '"titles":[{"name":"...","rank":1,"rationale":"...","industry":"..."}],'
            '"constraints":{"geo":"","remote":"","other":""},'
            '"confidence":"low|medium|high","notes":"..."}'
        )
        user_payload = {
            "fitMode": "recommend",
            "mode": found.get("mode"),
            "vaultSummary": found.get("vaultSummary") or {},
            "transcript": messages,
        }
        raw = await llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
            max_tokens=1600,
            temperature=0.35,
        )
        draft = self._normalize_fit_draft(self._parse_json_object(raw))
        found["draft"] = draft
        found["status"] = "draft"
        found["readyForRecommend"] = True
        saved = self.store.save_fit_session(session_id, found, owner=owner)
        return {"ok": True, "session": saved, "draft": draft}

    def fit_accept(
        self,
        *,
        session_id: str,
        confirm: bool = False,
        update_role_spectrum: bool = True,
        owner: str = SHARED_OWNER,
    ) -> dict[str, Any]:
        if not confirm:
            raise RuntimeError("confirm=true is required to accept career-fit into the knowledge bank")
        found = self.store.get_fit_session(session_id, owner)
        if found is None:
            raise RuntimeError(f"fit session not found: {session_id}")
        draft = found.get("draft")
        if not isinstance(draft, dict) or not draft.get("industries"):
            raise RuntimeError("no career-fit draft to accept; call recommend first")
        accepted_at = _now()
        career_fit = {
            "industries": draft.get("industries") or [],
            "titles": draft.get("titles") or [],
            "constraints": draft.get("constraints") or {},
            "confidence": draft.get("confidence") or "medium",
            "notes": draft.get("notes") or "",
            "acceptedAt": accepted_at,
            "sourceSessionId": session_id,
        }
        truth = self.store.get_truth(owner)
        truth["careerFit"] = career_fit
        if update_role_spectrum:
            industries = ", ".join(
                str(i.get("name"))
                for i in (career_fit.get("industries") or [])
                if isinstance(i, dict) and i.get("name")
            )
            titles = ", ".join(
                str(t.get("name"))
                for t in (career_fit.get("titles") or [])[:5]
                if isinstance(t, dict) and t.get("name")
            )
            bits = [p for p in (industries and f"Industries: {industries}", titles and f"Roles: {titles}") if p]
            notes = str(career_fit.get("notes") or "").strip()
            summary = ". ".join(bits)
            if notes:
                summary = f"{summary}. {notes}" if summary else notes
            if summary:
                spectrum = (
                    dict(truth.get("roleSpectrum") or {})
                    if isinstance(truth.get("roleSpectrum"), dict)
                    else {}
                )
                spectrum["summary"] = summary[:500]
                truth["roleSpectrum"] = spectrum
        saved_truth = self.store.save_truth(truth, owner=owner)
        found["status"] = "accepted"
        found["acceptedAt"] = accepted_at
        found["careerFit"] = career_fit
        saved = self.store.save_fit_session(session_id, found, owner=owner)
        return {
            "ok": True,
            "session": saved,
            "careerFit": career_fit,
            "truthVersion": saved_truth.get("version"),
        }
