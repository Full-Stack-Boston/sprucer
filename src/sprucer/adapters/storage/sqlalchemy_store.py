from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from sprucer.tenancy import SHARED_OWNER


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class Base(DeclarativeBase):
    pass


class TruthRow(Base):
    __tablename__ = "truth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_subject: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, default=SHARED_OWNER)
    document: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApplicationRow(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    owner_subject: Mapped[str] = mapped_column(String(200), nullable=False, default=SHARED_OWNER, index=True)
    company: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    source_type: Mapped[str] = mapped_column(String(40), default="paste")
    source_filename: Mapped[str] = mapped_column(String(400), default="")
    source_sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    notes: Mapped[str] = mapped_column(Text, default="")
    jd_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    generations: Mapped[list[GenerationRow]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="GenerationRow.created_at",
    )


class GenerationRow(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    types_json: Mapped[str] = mapped_column(Text, default="[]")
    emphasis_json: Mapped[str] = mapped_column(Text, default="[]")
    content: Mapped[str] = mapped_column(Text, default="")
    approval: Mapped[str] = mapped_column(String(40), default="draft")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    application: Mapped[ApplicationRow] = relationship(back_populates="generations")


class FitSessionRow(Base):
    """Career-fit interview session (draft until accept writes truth.careerFit)."""

    __tablename__ = "fit_sessions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_subject: Mapped[str] = mapped_column(String(200), nullable=False, index=True, default=SHARED_OWNER)
    status: Mapped[str] = mapped_column(String(40), default="interviewing")
    mode: Mapped[str] = mapped_column(String(40), default="inquire")
    document: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


EMPTY_TRUTH: dict[str, Any] = {
    "version": 1,
    "updatedAt": "",
    "profile": {},
    "headline": "",
    "voice": {"person": "first", "tone": "professional_not_bland", "punctuation": "keyboard_only"},
    "roleSpectrum": {},
    "careerFit": {},
    "education": {},
    "experience": [],
    "projects": [],
    "skills": {},
    "metrics": [],
    "neverClaim": [],
    "signatureStories": [],
    "blurbs": [],
    "resumePaths": {},
}


class SqlAlchemyStorage:
    """SQLite and Postgres via the same SQLAlchemy models."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            if database_url.startswith("sqlite:///./") or database_url.startswith("sqlite:////"):
                raw = database_url.removeprefix("sqlite:///")
                path = Path(raw)
                if not path.is_absolute():
                    path = Path.cwd() / path
                path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(database_url, future=True, connect_args=connect_args)
        self._Session = sessionmaker(self.engine, expire_on_commit=False, class_=Session)

    def ensure_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        self._ensure_generation_duration_column()
        self._ensure_owner_subject_columns()

    def _ensure_generation_duration_column(self) -> None:
        try:
            cols = {c["name"] for c in inspect(self.engine).get_columns("generations")}
        except Exception:
            return
        if "duration_ms" in cols:
            return
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "ALTER TABLE generations ADD COLUMN duration_ms INTEGER"
            )

    def _ensure_owner_subject_columns(self) -> None:
        """Add owner_subject to legacy DBs and backfill to shared."""
        for table in ("truth", "applications"):
            try:
                cols = {c["name"] for c in inspect(self.engine).get_columns(table)}
            except Exception:
                continue
            if "owner_subject" in cols:
                continue
            with self.engine.begin() as conn:
                # ``table`` comes only from the fixed tuple above, never user input.
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN owner_subject "
                    f"VARCHAR(200) NOT NULL DEFAULT '{SHARED_OWNER}'"
                )

    def get_truth(self, owner: str = SHARED_OWNER) -> dict[str, Any]:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            row = session.scalar(select(TruthRow).where(TruthRow.owner_subject == owner).limit(1))
            if row is None:
                doc = dict(EMPTY_TRUTH)
                doc["updatedAt"] = _now().isoformat()
                session.add(
                    TruthRow(
                        owner_subject=owner,
                        document=json.dumps(doc, ensure_ascii=True),
                        updated_at=_now(),
                    )
                )
                session.commit()
                return doc
            return json.loads(row.document)

    def save_truth(self, truth: dict[str, Any], owner: str = SHARED_OWNER) -> dict[str, Any]:
        owner = owner or SHARED_OWNER
        truth = dict(truth)
        truth["updatedAt"] = _now().isoformat()
        try:
            truth["version"] = int(truth.get("version") or 1) + 1
        except Exception:
            truth["version"] = 1
        with self._Session() as session:
            row = session.scalar(select(TruthRow).where(TruthRow.owner_subject == owner).limit(1))
            payload = json.dumps(truth, ensure_ascii=True, indent=2)
            if row is None:
                session.add(
                    TruthRow(owner_subject=owner, document=payload, updated_at=_now())
                )
            else:
                row.document = payload
                row.updated_at = _now()
            session.commit()
        return truth

    def _app_dict(self, row: ApplicationRow, *, include_jd: bool = False) -> dict[str, Any]:
        gens = [
            {
                "id": g.id,
                "createdAt": _iso(g.created_at),
                "types": json.loads(g.types_json or "[]"),
                "emphasisPlan": json.loads(g.emphasis_json or "[]"),
                "approval": g.approval,
                "approvedAt": _iso(g.approved_at),
                "durationMs": g.duration_ms,
                "files": [],
            }
            for g in (row.generations or [])
        ]
        out: dict[str, Any] = {
            "id": row.id,
            "company": row.company,
            "title": row.title,
            "location": row.location,
            "url": row.url,
            "sourceType": row.source_type,
            "sourceFilename": row.source_filename,
            "sourceSha256": row.source_sha256,
            "status": row.status,
            "notes": row.notes,
            "createdAt": _iso(row.created_at),
            "updatedAt": _iso(row.updated_at),
            "generations": gens,
        }
        if include_jd:
            out["jdText"] = row.jd_text
        return out

    def _get_app_row(self, session: Session, application_id: str, owner: str) -> ApplicationRow | None:
        row = session.get(ApplicationRow, application_id)
        if row is None or row.owner_subject != owner:
            return None
        return row

    def list_applications(self, owner: str = SHARED_OWNER) -> list[dict[str, Any]]:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            rows = session.scalars(
                select(ApplicationRow)
                .where(ApplicationRow.owner_subject == owner)
                .order_by(ApplicationRow.updated_at.desc())
            ).all()
            return [self._app_dict(r) for r in rows]

    def get_application(self, application_id: str, owner: str = SHARED_OWNER) -> dict[str, Any] | None:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            row = self._get_app_row(session, application_id, owner)
            if row is None:
                return None
            return self._app_dict(row, include_jd=True)

    def upsert_application(
        self, meta: dict[str, Any], *, jd_text: str | None = None, owner: str = SHARED_OWNER
    ) -> dict[str, Any]:
        owner = owner or SHARED_OWNER
        app_id = str(meta["id"])
        with self._Session() as session:
            row = session.get(ApplicationRow, app_id)
            now = _now()
            if row is not None and row.owner_subject != owner:
                raise KeyError(app_id)
            if row is None:
                row = ApplicationRow(id=app_id, owner_subject=owner, created_at=now)
                session.add(row)
            row.owner_subject = owner
            row.company = str(meta.get("company") or "")
            row.title = str(meta.get("title") or "")
            row.location = str(meta.get("location") or "")
            row.url = str(meta.get("url") or "")
            row.source_type = str(meta.get("sourceType") or meta.get("source_type") or "paste")
            row.source_filename = str(meta.get("sourceFilename") or "")
            row.source_sha256 = str(meta.get("sourceSha256") or "")
            row.status = str(meta.get("status") or "draft")
            row.notes = str(meta.get("notes") or "")
            if jd_text is not None:
                row.jd_text = jd_text
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._app_dict(row, include_jd=True)

    def delete_application(self, application_id: str, owner: str = SHARED_OWNER) -> None:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            row = self._get_app_row(session, application_id, owner)
            if row is None:
                raise KeyError(application_id)
            session.delete(row)
            session.commit()

    def purge_owner_prefix(self, prefix: str, older_than: datetime) -> int:
        """Delete truth + applications for owners matching ``prefix`` last touched before ``older_than``.

        Used to sweep stale per-session demo vaults. Returns the number of owners purged.
        """
        prefix = (prefix or "").strip()
        if not prefix:
            return 0
        like = prefix.replace("%", r"\%").replace("_", r"\_") + "%"
        purged = 0
        with self._Session() as session:
            apps = session.scalars(
                select(ApplicationRow).where(
                    ApplicationRow.owner_subject.like(like, escape="\\"),
                    ApplicationRow.updated_at < older_than,
                )
            ).all()
            for row in apps:
                session.delete(row)
            truths = session.scalars(
                select(TruthRow).where(
                    TruthRow.owner_subject.like(like, escape="\\"),
                    TruthRow.updated_at < older_than,
                )
            ).all()
            for row in truths:
                session.delete(row)
                purged += 1
            fits = session.scalars(
                select(FitSessionRow).where(
                    FitSessionRow.owner_subject.like(like, escape="\\"),
                    FitSessionRow.updated_at < older_than,
                )
            ).all()
            for row in fits:
                session.delete(row)
            session.commit()
        return purged

    def get_jd(self, application_id: str, owner: str = SHARED_OWNER) -> str:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            row = self._get_app_row(session, application_id, owner)
            if row is None:
                raise KeyError(application_id)
            return row.jd_text or ""

    def list_generations(self, application_id: str, owner: str = SHARED_OWNER) -> list[dict[str, Any]]:
        app = self.get_application(application_id, owner=owner)
        if app is None:
            raise KeyError(application_id)
        return list(app.get("generations") or [])

    def get_generation(
        self, application_id: str, generation_id: str, owner: str = SHARED_OWNER
    ) -> dict[str, Any] | None:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            app = self._get_app_row(session, application_id, owner)
            if app is None:
                return None
            row = session.get(GenerationRow, generation_id)
            if row is None or row.application_id != application_id:
                return None
            return {
                "id": row.id,
                "createdAt": _iso(row.created_at),
                "types": json.loads(row.types_json or "[]"),
                "emphasisPlan": json.loads(row.emphasis_json or "[]"),
                "approval": row.approval,
                "approvedAt": _iso(row.approved_at),
                "durationMs": row.duration_ms,
                "content": row.content,
                "files": [],
            }

    def save_generation(
        self,
        application_id: str,
        *,
        generation: dict[str, Any],
        content: str,
        owner: str = SHARED_OWNER,
    ) -> dict[str, Any]:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            app = self._get_app_row(session, application_id, owner)
            if app is None:
                raise KeyError(application_id)
            gen_id = str(generation["id"])
            row = GenerationRow(
                id=gen_id,
                application_id=application_id,
                types_json=json.dumps(generation.get("types") or [], ensure_ascii=True),
                emphasis_json=json.dumps(generation.get("emphasisPlan") or [], ensure_ascii=True),
                content=content,
                approval=str(generation.get("approval") or "draft"),
                duration_ms=(
                    int(generation["durationMs"])
                    if generation.get("durationMs") is not None
                    else None
                ),
                created_at=_now(),
            )
            session.add(row)
            app.updated_at = _now()
            session.commit()
            return {
                "id": row.id,
                "createdAt": _iso(row.created_at),
                "types": generation.get("types") or [],
                "emphasisPlan": generation.get("emphasisPlan") or [],
                "approval": row.approval,
                "approvedAt": None,
                "durationMs": row.duration_ms,
                "files": [],
            }

    def approve_generation(
        self, application_id: str, generation_id: str, owner: str = SHARED_OWNER
    ) -> dict[str, Any]:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            app = self._get_app_row(session, application_id, owner)
            if app is None:
                raise KeyError(generation_id)
            row = session.get(GenerationRow, generation_id)
            if row is None or row.application_id != application_id:
                raise KeyError(generation_id)
            row.approval = "approved"
            row.approved_at = _now()
            session.commit()
            return {
                "id": row.id,
                "createdAt": _iso(row.created_at),
                "types": json.loads(row.types_json or "[]"),
                "emphasisPlan": json.loads(row.emphasis_json or "[]"),
                "approval": row.approval,
                "approvedAt": _iso(row.approved_at),
                "durationMs": row.duration_ms,
                "files": [],
            }

    def delete_generation(
        self, application_id: str, generation_id: str, owner: str = SHARED_OWNER
    ) -> None:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            app = self._get_app_row(session, application_id, owner)
            if app is None:
                raise KeyError(generation_id)
            row = session.get(GenerationRow, generation_id)
            if row is None or row.application_id != application_id:
                raise KeyError(generation_id)
            session.delete(row)
            app.updated_at = _now()
            session.commit()

    def _fit_dict(self, row: FitSessionRow) -> dict[str, Any]:
        try:
            doc = json.loads(row.document or "{}")
        except Exception:
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        return {
            "id": row.id,
            "status": row.status,
            "mode": row.mode,
            "createdAt": _iso(row.created_at),
            "updatedAt": _iso(row.updated_at),
            **doc,
        }

    def get_fit_session(self, session_id: str, owner: str = SHARED_OWNER) -> dict[str, Any] | None:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            row = session.get(FitSessionRow, session_id)
            if row is None or row.owner_subject != owner:
                return None
            return self._fit_dict(row)

    def save_fit_session(
        self, session_id: str, payload: dict[str, Any], *, owner: str = SHARED_OWNER
    ) -> dict[str, Any]:
        owner = owner or SHARED_OWNER
        payload = dict(payload)
        status = str(payload.get("status") or "interviewing")
        mode = str(payload.get("mode") or "inquire")
        # Persist mutable fields in document; id/status/mode also mirrored on columns.
        doc = {
            k: v
            for k, v in payload.items()
            if k not in ("id", "createdAt", "updatedAt")
        }
        doc["status"] = status
        doc["mode"] = mode
        with self._Session() as session:
            row = session.get(FitSessionRow, session_id)
            now = _now()
            if row is None:
                row = FitSessionRow(
                    id=session_id,
                    owner_subject=owner,
                    status=status,
                    mode=mode,
                    document=json.dumps(doc, ensure_ascii=True),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                if row.owner_subject != owner:
                    raise KeyError(session_id)
                row.status = status
                row.mode = mode
                row.document = json.dumps(doc, ensure_ascii=True)
                row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._fit_dict(row)

    def list_fit_sessions(self, owner: str = SHARED_OWNER, *, limit: int = 20) -> list[dict[str, Any]]:
        owner = owner or SHARED_OWNER
        with self._Session() as session:
            rows = session.scalars(
                select(FitSessionRow)
                .where(FitSessionRow.owner_subject == owner)
                .order_by(FitSessionRow.updated_at.desc())
                .limit(max(1, min(limit, 100)))
            ).all()
            return [self._fit_dict(r) for r in rows]


def create_storage(database_url: str) -> SqlAlchemyStorage:
    store = SqlAlchemyStorage(database_url)
    store.ensure_schema()
    return store
