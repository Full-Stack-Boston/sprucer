"""Exercise defensive and ownership edges that the coverage gate must preserve."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import update
from starlette.requests import Request

from sprucer.adapters.auth import DemoAuth, OidcAuth, create_auth
from sprucer.adapters.llm import DemoLlm, MockLlm
from sprucer.adapters.oidc import OidcClient
from sprucer.adapters.storage import create_storage
from sprucer.adapters.storage.sqlalchemy_store import FitSessionRow, SqlAlchemyStorage, TruthRow
from sprucer.api.app import build_app
from sprucer.service import (
    CareerService,
    _extract_html_jd,
    _find_workday_job_url,
    _guess_fields,
)
from sprucer.settings import Settings


def _request(*, authorization: str = "", cookie: str = "") -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request({"type": "http", "headers": headers})


def _service(tmp_path: Path) -> CareerService:
    return CareerService(create_storage(f"sqlite:///{tmp_path / 'hardening.db'}"), MockLlm())


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": f"sqlite:///{tmp_path / 'api.db'}",
        "auth_mode": "dev",
        "host": "127.0.0.1",
        "dev_password": "password",
        "session_secret": "hardening-secret-value",
        "llm_url": "http://llm.invalid/v1",
        "llm_api_key": "key",
        "byok_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_auth_invalid_cookie_fallthroughs():
    demo = DemoAuth(session_secret="secret")
    with pytest.raises(HTTPException, match="Demo session"):
        demo.authenticate(_request(cookie=f"{demo.cookie_name}=invalid"))

    oidc = OidcAuth(
        issuer="https://issuer.example",
        client_id="client",
        client_secret="secret",
        redirect_uri="https://app.example/callback",
        session_secret="session-secret",
        api_keys={"valid"},
    )
    with pytest.raises(HTTPException, match="OIDC session"):
        oidc.authenticate(_request(authorization="Bearer invalid"))
    with pytest.raises(HTTPException, match="OIDC session"):
        oidc.authenticate(_request(cookie=f"{oidc.cookie_name}=invalid"))


@pytest.mark.asyncio
async def test_llm_collection_shape_edges():
    mock = MockLlm()
    reply = await mock.chat(
        [
            {
                "role": "user",
                "content": json.dumps(
                    {"artifactTypes": ["interview"], "interviewCiteCatalog": "not-a-list"}
                ),
            }
        ]
    )
    assert "Interview Prep" in reply

    recommendation = await mock.chat(
        [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "fitMode": "recommend",
                        "vaultSummary": {"roleSpectrum": "legacy-string"},
                    }
                ),
            }
        ]
    )
    assert json.loads(recommendation)["industries"]

    demo = DemoLlm()
    await demo.chat(
        [
            {"role": "user", "content": json.dumps({"unrelated": True})},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "artifactTypes": ["cover"],
                        "careerTruth": {
                            "metrics": ["bad", {"text": ""}],
                            "blurbs": ["bad", {"label": ""}],
                            "experience": ["bad", {"role": "", "org": ""}],
                        },
                    }
                ),
            },
        ]
    )
    assert demo.calls


@pytest.mark.asyncio
async def test_oidc_userinfo_optional_branches(monkeypatch: pytest.MonkeyPatch):
    class Result:
        def __init__(self, status: int, data: dict[str, object]):
            self.status_code = status
            self._data = data
            self.text = "error"

        def json(self):
            return self._data

    class Client:
        userinfo = Result(500, {})
        tokens: dict[str, object] = {"access_token": "access", "sub": "token-sub"}

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return Result(200, self.tokens)

        async def get(self, *args, **kwargs):
            return self.userinfo

    monkeypatch.setattr("sprucer.adapters.oidc.httpx.AsyncClient", Client)
    client = OidcClient(
        issuer="https://issuer.example",
        client_id="client",
        client_secret="secret",
        redirect_uri="https://app.example/callback",
    )
    client._discovery = {
        "token_endpoint": "https://issuer.example/token",
        "userinfo_endpoint": "https://issuer.example/userinfo",
    }
    assert (await client.exchange_code("code"))["sub"] == "token-sub"

    Client.userinfo = Result(200, {"sub": "userinfo-sub"})
    Client.tokens = {"access_token": "access", "sub": "token-sub"}
    assert (await client.exchange_code("code"))["sub"] == "token-sub"


def test_storage_legacy_paths_corrupt_sessions_and_wrong_owner(tmp_path: Path):
    postgres = SqlAlchemyStorage("postgresql+psycopg://user:pass@localhost/db")
    postgres.engine.dispose()
    relative = create_storage(f"sqlite:///./{tmp_path.name}-relative.db")
    relative.engine.dispose()
    memory = create_storage("sqlite:///:memory:")
    memory.engine.dispose()

    store = create_storage(f"sqlite:///{tmp_path / 'storage.db'}")
    old = datetime.now(tz=timezone.utc) - timedelta(days=2)
    store.save_truth({"profile": {}}, owner="demo-old")
    store.upsert_application({"id": "old-app"}, owner="demo-old")
    store.save_fit_session("old-fit", {"status": "interviewing"}, owner="demo-old")
    with store.engine.begin() as conn:
        conn.execute(
            update(TruthRow)
            .where(TruthRow.owner_subject == "demo-old")
            .values(updated_at=old)
        )
        conn.execute(
            update(FitSessionRow)
            .where(FitSessionRow.id == "old-fit")
            .values(updated_at=old)
        )
        conn.exec_driver_sql(
            "UPDATE applications SET updated_at = ? WHERE id = ?",
            (old.replace(tzinfo=None), "old-app"),
        )
    assert store.purge_owner_prefix("demo-", datetime.now(tz=timezone.utc) - timedelta(hours=1)) == 1
    assert store.get_fit_session("old-fit", "demo-old") is None

    store.save_fit_session("corrupt", {"status": "interviewing"}, owner="alice")
    with store.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE fit_sessions SET document = ? WHERE id = ?",
            ("not-json", "corrupt"),
        )
    assert store.get_fit_session("corrupt", "alice")["status"] == "interviewing"
    with store.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE fit_sessions SET document = ? WHERE id = ?",
            ("[]", "corrupt"),
        )
    assert store.get_fit_session("corrupt", "alice")["mode"] == "inquire"
    assert store.get_fit_session("corrupt", "bob") is None
    with pytest.raises(KeyError):
        store.save_fit_session("corrupt", {"status": "draft"}, owner="bob")


@pytest.mark.asyncio
async def test_service_helper_and_validation_edges(tmp_path: Path):
    svc = _service(tmp_path)
    assert svc.truth_patch(op="add", section="neverClaim", item="plain text")["ok"]
    with pytest.raises(RuntimeError, match="requires text"):
        svc.truth_patch(op="add", section="neverClaim", item=7)  # type: ignore[arg-type]
    assert svc.truth_patch(
        op="add", section="metrics", item={"id": "existing", "text": "kept"}
    )["truth"]["metrics"][-1]["id"] == "existing"

    _, fields = _extract_html_jd("<title> </title><p>short</p>")
    assert fields["title"] == ""
    one, fields = _extract_html_jd("<title>Engineer</title><p>" + "x" * 2600 + "</p>")
    assert one and fields["title"] == "Engineer"
    _, fields = _extract_html_jd(
        '<meta property="og:title" content="Platform Engineer"><p>short</p>'
    )
    assert fields["title"] == "Platform Engineer"
    _, fields = _extract_html_jd(
        '<title>Existing | Boston</title><meta property="og:title" content="Fallback"><p>x</p>'
    )
    assert fields["title"] == "Existing"
    assert _find_workday_job_url(
        "https://acme.wd5.myworkdayjobs.com/en-US/site/job/Boston/Engineer"
    ).endswith("/Engineer")
    guessed = _guess_fields(
        "skip to main content\nhttps://jobs.example\nxx\n" + "z" * 121,
        url="https://jobs",
    )
    assert guessed["title"] == ""
    assert guessed["company"] == ""

    assert svc._content_satisfies_types(
        "## Email\nSubject: x\n## Resume\n## Cover Letter",
        ["email", "resume"],
    )
    assert svc._content_satisfies_types(
        "## LinkedIn DM\n## Resume\n" + "\n- item" * 9,
        ["linkedin", "resume"],
    )
    assert not svc._content_satisfies_types(
        "## Interview Prep\n### Questions\n? ? ?",
        ["interview"],
    )
    assert svc._content_satisfies_types(
        "## Interview Prep\n### Questions\n? ? ?\na b c",
        ["interview"],
        cite_ids={"a", "b", "c"},
    )
    assert svc._type_batches(["cover", "email"]) == [["cover", "email"]]
    assert svc._type_batches(["interview"]) == [["interview"]]
    assert svc._type_batches(["interview"] * 3) == [["interview"]] * 3

    catalog = svc._interview_cite_catalog(
        {
            "metrics": ["bad", {"id": "m", "text": "Metric"}],
            "projects": ["bad", {"id": "p", "title": "Project"}],
            "experience": ["bad", {"id": "e", "role": "Engineer"}],
            "signatureStories": ["bad", {"id": "s", "title": "Story"}],
            "blurbs": ["bad", {"id": "b", "text": "Blurb"}],
        }
    )
    assert {row["id"] for row in catalog} == {"m", "p", "e", "s", "b"}

    svc.store.upsert_application({"id": "first", "url": "https://other.example"})
    svc.store.upsert_application({"id": "reuse", "url": "https://jobs.example/role"})
    svc._fetch_url_text = AsyncMock(return_value=("Title: Engineer", {}))  # type: ignore[method-assign]
    reused = await svc.jd_ingest(source_type="url", url="https://jobs.example/role#apply")
    assert reused["application"]["id"] == "reuse"
    created = await svc.jd_ingest(source_type="url", url="https://jobs.example/new-role")
    assert created["application"]["id"] not in {"first", "reuse"}

    class FetchResponse:
        status_code = 200
        headers = {"content-type": "text/plain"}
        text = "Title: Engineer"
        content = b""

    fetch_svc = _service(tmp_path / "fetch")
    fetch_svc._fetch_workday_cxs = AsyncMock(return_value=None)  # type: ignore[method-assign]
    fetch_svc._safe_get = AsyncMock(return_value=FetchResponse())  # type: ignore[method-assign]
    text, _ = await fetch_svc._fetch_url_text(
        "https://acme.wd5.myworkdayjobs.com/en-US/site/job/Boston/Engineer"
    )
    assert "Engineer" in text

    FetchResponse.headers = {"content-type": "text/html"}
    FetchResponse.text = (
        "<html><body>"
        "https://acme.wd5.myworkdayjobs.com/en-US/site/job/Boston/Engineer"
        "<p>Fallback body</p></body></html>"
    )
    text, _ = await fetch_svc._fetch_url_text("https://careers.example/role")
    assert "Fallback body" in text


def test_fit_normalization_openings_and_json_edges(tmp_path: Path):
    svc = _service(tmp_path)
    summary = svc._fit_vault_summary(
        {
            "experience": ["legacy", {"role": "", "org": ""}],
            "skills": {"bad": "string", "good": ["one"]},
            "careerFit": {"industries": [{"name": "Cloud"}]},
        }
    )
    assert svc._fit_has_existing_prefs(summary)
    assert not svc._fit_has_existing_prefs(
        {"careerFit": {"industries": "bad", "titles": "bad"}, "roleSpectrum": "bad"}
    )
    opening = svc._fit_opening_message(
        mode="confirm",
        summary={
            "careerFit": {
                "industries": [{"name": "Cloud"}, "SaaS", 7],
                "titles": [{"name": "Platform Engineer"}, "SRE", None],
            },
            "roleSpectrum": {"summary": "Broad operator"},
        },
    )
    assert "Cloud" in opening and "Platform Engineer" in opening
    spectrum_only = svc._fit_opening_message(
        mode="confirm",
        summary={"careerFit": {}, "roleSpectrum": {"summary": "Still valid"}},
    )
    assert "No accepted careerFit" in spectrum_only

    with pytest.raises(RuntimeError, match="empty"):
        svc._parse_json_object("")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        svc._parse_json_object("[]")
    assert svc._parse_json_object("[]\n```json\n{\"a\": 1}\n```") == {"a": 1}
    assert svc._parse_json_object("prefix {\"b\": 2} suffix") == {"b": 2}
    with pytest.raises(RuntimeError, match="not valid JSON"):
        svc._parse_json_object("nothing useful")

    assert svc._normalize_ranked_items("bad") == []
    ranked = svc._normalize_ranked_items(["", "Cloud", 7, {}, {"title": "SRE", "rank": 9}])
    assert [row["name"] for row in ranked] == ["Cloud", "SRE"]
    with pytest.raises(RuntimeError, match="at least one"):
        svc._normalize_fit_draft({"industries": []})
    draft = svc._normalize_fit_draft(
        {
            "industries": ["Cloud"],
            "constraints": "legacy",
            "confidence": "certain",
        }
    )
    assert draft["confidence"] == "medium"


@pytest.mark.asyncio
async def test_fit_service_error_and_state_edges(tmp_path: Path):
    svc = _service(tmp_path)
    with pytest.raises(RuntimeError, match="not found"):
        svc.fit_get(session_id="missing")
    with pytest.raises(RuntimeError, match="message is required"):
        await svc.fit_turn(session_id="missing", message=" ")
    with pytest.raises(RuntimeError, match="not found"):
        await svc.fit_turn(session_id="missing", message="answer")

    started = svc.fit_start()["session"]
    session_id = started["id"]
    assert svc.fit_get(session_id=session_id)["session"]["id"] == session_id
    started["status"] = "accepted"
    svc.store.save_fit_session(session_id, started)
    with pytest.raises(RuntimeError, match="already accepted"):
        await svc.fit_turn(session_id=session_id, message="answer")

    started["status"] = "draft"
    svc.store.save_fit_session(session_id, started)
    bad = MockLlm(reply='{"readyForRecommend": true}')
    with pytest.raises(RuntimeError, match="assistantMessage"):
        await svc.fit_turn(session_id=session_id, message="answer", llm_override=bad)
    good = MockLlm(reply='{"assistantMessage": "Next?", "readyForRecommend": false}')
    turned = await svc.fit_turn(session_id=session_id, message="answer", llm_override=good)
    assert turned["session"]["status"] == "interviewing"

    with pytest.raises(RuntimeError, match="not found"):
        await svc.fit_recommend(session_id="missing", llm_override=good)
    turned["session"]["status"] = "accepted"
    svc.store.save_fit_session(session_id, turned["session"])
    with pytest.raises(RuntimeError, match="already accepted"):
        await svc.fit_recommend(session_id=session_id, llm_override=good)

    fresh = svc.fit_start()["session"]
    with pytest.raises(RuntimeError, match="answer at least one"):
        await svc.fit_recommend(session_id=fresh["id"], llm_override=good)

    with pytest.raises(RuntimeError, match="not found"):
        svc.fit_accept(session_id="missing", confirm=True)
    with pytest.raises(RuntimeError, match="no career-fit draft"):
        svc.fit_accept(session_id=fresh["id"], confirm=True)

    fresh["draft"] = {
        "industries": [{"name": "Cloud"}],
        "titles": [],
        "constraints": {},
        "confidence": "low",
        "notes": "",
    }
    svc.store.save_fit_session(fresh["id"], fresh)
    accepted = svc.fit_accept(
        session_id=fresh["id"],
        confirm=True,
        update_role_spectrum=False,
    )
    assert accepted["session"]["status"] == "accepted"

    no_summary = svc.fit_start()["session"]
    no_summary["draft"] = {
        "industries": [{}],
        "titles": [{}],
        "constraints": {},
        "confidence": "low",
        "notes": "",
    }
    svc.store.save_fit_session(no_summary["id"], no_summary)
    assert svc.fit_accept(session_id=no_summary["id"], confirm=True)["ok"]


def test_fit_api_error_mappings(tmp_path: Path):
    settings = _settings(tmp_path)
    auth = create_auth(
        mode="dev",
        dev_password=settings.dev_password,
        session_secret=settings.session_secret,
        api_keys=set(),
    )
    service = MagicMock()
    service.fit_start.side_effect = RuntimeError("start failed")
    service.fit_get.side_effect = RuntimeError("missing")
    service.fit_turn.side_effect = RuntimeError("turn failed")
    service.fit_recommend.side_effect = RuntimeError("recommend failed")
    app = build_app(settings=settings, service=service, auth=auth)

    with TestClient(app) as client:
        assert client.post("/v1/auth/login", json={"password": settings.dev_password}).status_code == 200
        assert client.post("/v1/fit/start").status_code == 400
        assert client.get("/v1/fit/sessions/missing").status_code == 404

        invalid_headers = {
            "x-llm-base-url": "http://private.invalid",
            "x-llm-api-key": "secret",
        }
        assert client.post(
            "/v1/fit/turn",
            json={"session_id": "s", "message": "m"},
            headers=invalid_headers,
        ).status_code == 400
        assert client.post(
            "/v1/fit/recommend",
            json={"session_id": "s"},
            headers=invalid_headers,
        ).status_code == 400
        assert client.post(
            "/v1/fit/turn",
            json={"session_id": "s", "message": "m"},
        ).status_code == 400
        assert client.post(
            "/v1/fit/recommend",
            json={"session_id": "s"},
        ).status_code == 400
