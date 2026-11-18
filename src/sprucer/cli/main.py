from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from rich import print as rprint

from sprucer.adapters.filesystem_import import import_filesystem_vault
from sprucer.adapters.storage import create_storage

app = typer.Typer(help="Sprucer CLI: thin client for the Sprucer brain API.", no_args_is_help=True)


def _client(base_url: str, password: str | None, api_key: str | None) -> httpx.Client:
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=180.0, trust_env=False)
    if password and not api_key:
        res = client.post("/v1/auth/login", json={"password": password})
        res.raise_for_status()
    return client


def _base(
    ctx: typer.Context,
) -> tuple[str, str | None, str | None]:
    return ctx.obj["base_url"], ctx.obj.get("password"), ctx.obj.get("api_key")


@app.callback()
def main(
    ctx: typer.Context,
    base_url: str = typer.Option("http://127.0.0.1:8787", "--url", envvar="SPRUCER_PUBLIC_URL"),
    password: Optional[str] = typer.Option(None, "--password", envvar="SPRUCER_DEV_PASSWORD"),
    api_key: Optional[str] = typer.Option(None, "--api-key", envvar="SPRUCER_API_KEY"),
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["password"] = password
    ctx.obj["api_key"] = api_key


@app.command("health")
def health(ctx: typer.Context) -> None:
    base, password, api_key = _base(ctx)
    with _client(base, password, api_key) as client:
        rprint(client.get("/health").json())


@app.command("truth")
def truth(ctx: typer.Context) -> None:
    base, password, api_key = _base(ctx)
    with _client(base, password, api_key) as client:
        rprint(client.get("/v1/truth").json())


@app.command("list")
def list_apps(ctx: typer.Context) -> None:
    base, password, api_key = _base(ctx)
    with _client(base, password, api_key) as client:
        rprint(client.get("/v1/applications").json())


@app.command("ingest")
def ingest(
    ctx: typer.Context,
    paste: Optional[Path] = typer.Option(None, "--paste", help="Path to JD text file"),
    url: Optional[str] = typer.Option(None, "--url"),
) -> None:
    base, password, api_key = _base(ctx)
    if paste:
        body: dict[str, Any] = {"source_type": "paste", "text": paste.read_text(encoding="utf-8")}
    elif url:
        body = {"source_type": "url", "url": url}
    else:
        raise typer.BadParameter("Provide --paste or --url")
    with _client(base, password, api_key) as client:
        rprint(client.post("/v1/jd/ingest", json=body).json())


@app.command("generate")
def generate(
    ctx: typer.Context,
    application_id: str,
    types: str = typer.Option("cover,email", "--types"),
) -> None:
    base, password, api_key = _base(ctx)
    type_list = [t.strip() for t in types.split(",") if t.strip()]
    with _client(base, password, api_key) as client:
        rprint(
            client.post(
                "/v1/generate",
                json={"application_id": application_id, "types": type_list},
            ).json()
        )


@app.command("approve")
def approve(ctx: typer.Context, application_id: str, generation_id: str) -> None:
    base, password, api_key = _base(ctx)
    with _client(base, password, api_key) as client:
        rprint(
            client.post(
                "/v1/generations/approve",
                json={
                    "application_id": application_id,
                    "generation_id": generation_id,
                    "confirm": True,
                },
            ).json()
        )


@app.command("import-vault")
def import_vault(
    vault_root: Path,
    database_url: str = typer.Option("sqlite:///./data/sprucer.db", "--database-url"),
) -> None:
    store = create_storage(database_url)
    result = import_filesystem_vault(vault_root, store)
    rprint(result)


@app.command("load-fixture")
def load_fixture(
    database_url: str = typer.Option("sqlite:///./data/sprucer.db", "--database-url"),
    truth: Path = typer.Option(
        Path("fixtures/synthetic/career-truth.json"),
        "--truth",
    ),
) -> None:
    store = create_storage(database_url)
    doc = json.loads(truth.read_text(encoding="utf-8"))
    store.save_truth(doc)
    rprint({"ok": True, "loaded": str(truth)})


if __name__ == "__main__":
    app()
