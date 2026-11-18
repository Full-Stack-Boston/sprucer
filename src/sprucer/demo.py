"""Self-contained demo data + helpers.

Everything the demo needs is embedded here so a demo container has no external
assumptions (no fixture files to mount, no seed service, no network). Each demo
visitor gets their own copy seeded into an isolated per-session vault.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from sprucer.tenancy import owner_key

if TYPE_CHECKING:  # pragma: no cover
    from sprucer.adapters.storage.sqlalchemy_store import SqlAlchemyStorage

DEMO_TRUTH: dict[str, Any] = {
    "version": 1,
    "updatedAt": "2026-01-01T00:00:00.000Z",
    "profile": {
        "name": "Alex Rivera",
        "shortName": "Alex",
        "location": "Portland, OR",
        "email": "alex@example.com",
        "site": "https://example.com",
        "languages": ["English"],
        "honors": [],
    },
    "headline": "Platform engineer who keeps small teams shipping",
    "voice": {
        "person": "first",
        "tone": "professional_not_bland",
        "punctuation": "keyboard_only",
        "notes": "Direct, concrete, no hype adjectives.",
    },
    "roleSpectrum": {
        "summary": (
            "Platform, SRE-adjacent, and full-stack ops roles where ownership of "
            "reliability and developer experience matters."
        ),
        "preferSenior": True,
        "titleNamesIrrelevant": True,
        "emphasizeDynamically": True,
    },
    "education": {
        "degree": "B.S. Computer Science",
        "detail": "Systems concentration",
        "school": "Example State University",
        "graduated": "June 2016",
    },
    "experience": [
        {
            "id": "exp-northwind",
            "org": "Northwind Labs",
            "role": "Senior Platform Engineer",
            "dates": "Mar 2021 to present",
            "points": [
                "Owned CI and deploy pipelines for 12 services with zero Friday-fire pages last two quarters",
                "Cut average PR time-to-prod from 2 days to under 4 hours with trunk-based checks",
            ],
        },
        {
            "id": "exp-contoso",
            "org": "Contoso Retail",
            "role": "Software Engineer",
            "dates": "Jul 2016 to Feb 2021",
            "points": [
                "Built inventory sync jobs processing 2M SKU updates nightly",
                "Introduced on-call runbooks that reduced mean acknowledge time by half",
            ],
        },
    ],
    "projects": [
        {
            "id": "proj-desk",
            "name": "Desklight",
            "summary": "Open-source local job-application desk used in demos",
            "tags": ["oss", "python"],
        }
    ],
    "skills": {
        "frontend": ["TypeScript", "React"],
        "backend": ["Python", "FastAPI", "Postgres"],
        "cloudOps": ["Docker", "Linux", "CI"],
        "practice": ["Incident response", "Technical writing"],
    },
    "metrics": [
        {
            "id": "metric-pr-speed",
            "text": "Cut average PR time-to-prod from 2 days to under 4 hours",
            "tags": ["ci", "dx"],
        },
        {
            "id": "metric-pager",
            "text": "Zero Friday-fire pager pages across two quarters for owned services",
            "tags": ["reliability"],
        },
    ],
    "neverClaim": [
        "Do not claim management of people or budget without an explicit truth entry",
        "Do not invent cloud certifications",
    ],
    "signatureStories": [
        {
            "id": "story-freeze",
            "title": "The deploy freeze that was not",
            "body": (
                "When a holiday freeze conflicted with a critical security patch, I negotiated a "
                "narrow change window, automated the rollback drill first, and landed the patch "
                "with stakeholders watching the health board."
            ),
            "tags": ["incident", "communication"],
        }
    ],
    "blurbs": [
        {
            "id": "blurb-dx",
            "label": "Developer experience",
            "text": "I care about the path from pull request to production being boring in the best way.",
            "tags": ["dx"],
        }
    ],
    "resumePaths": {},
}

DEMO_JD = (
    "Senior Platform Engineer\n"
    "Acme Cloud: Remote (US)\n\n"
    "Acme Cloud builds observability tooling for mid-size SaaS teams. We are hiring a Senior "
    "Platform Engineer to own CI/CD, developer experience, and reliability for our control plane.\n\n"
    "Responsibilities:\n"
    "- Maintain and improve deploy pipelines for a multi-service Python/TypeScript stack\n"
    "- Partner with product engineers on incident response and runbooks\n"
    "- Reduce time from pull request to production without sacrificing safety\n"
    "- Mentor peers on operational ownership\n\n"
    "Requirements:\n"
    "- 5+ years building and operating production services\n"
    "- Strong Linux, containers, and CI experience\n"
    "- Comfortable with Postgres and API services\n"
    "- Clear written communication\n"
)


def seed_demo_vault(store: SqlAlchemyStorage, owner: str) -> str:
    """Seed a fresh per-session demo vault with synthetic truth + one sample application."""
    store.save_truth(copy.deepcopy(DEMO_TRUTH), owner=owner)
    app_id = f"{owner_key(owner)}-sample-application"
    store.upsert_application(
        {
            "id": app_id,
            "company": "Acme Cloud",
            "title": "Senior Platform Engineer",
            "location": "Remote (US)",
            "sourceType": "paste",
            "status": "draft",
        },
        jd_text=DEMO_JD,
        owner=owner,
    )
    return app_id
