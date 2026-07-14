"""Read-only Palliativa ticket delivery map backed by api-bridges."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field


API_BRIDGES_BASE_URL = "https://api-bridges.ryzen.jjrsoftware.co.uk"
JIRA_BASE_URL = "https://palliativa.atlassian.net"
JIRA_SESSION = "codex"
JIRA_PROJECT = "AP"
FEATURE_BUILDS_FIELD = "customfield_10308"
GITHUB_OWNER = "palliativa"
GITHUB_REPO = "monorepo"
GITHUB_BASE = "master"
SEMVER_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
UPSTREAM_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

logger = logging.getLogger(__name__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReleaseLayer(StrictModel):
    version: str
    url: str


class MasterLayer(StrictModel):
    url: str
    commits_since_release: int = Field(ge=0)
    tickets_in_master: int = Field(ge=0)


class PullRequestLink(StrictModel):
    title: str
    url: str
    merged: bool


class DeploymentView(StrictModel):
    env: str
    label: str
    app_url: str
    deploy_kind: Literal["image", "mounted"] | None
    image_tag: str
    healthy: bool
    error: str = ""


TicketPosition = Literal["pull_request", "master", "merged_not_master", "built", "ticket_only"]


class TicketView(StrictModel):
    key: str
    summary: str
    url: str
    status: str
    fix_versions: list[str]
    position: TicketPosition
    pull_requests: list[PullRequestLink]
    feature_builds: list[str]
    deployments: list[str]
    delivery_error: str = ""


class DeliveryStackResponse(StrictModel):
    generated_at: datetime
    release: ReleaseLayer
    master: MasterLayer
    deployments: list[DeploymentView]
    tickets: list[TicketView]


class BridgeError(RuntimeError):
    pass


app = FastAPI(
    title="Palliativa Delivery Map",
    description="Read-only release, master, ticket, build, pull-request, and deployment evidence.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://jira.dev.jjrsoftware.co.uk"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


async def _bridge_request(
    client: httpx.AsyncClient,
    method: Literal["GET", "POST"],
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    try:
        response = await client.request(
            method,
            f"{API_BRIDGES_BASE_URL}{path}",
            json=payload,
        )
    except httpx.RequestError as exc:
        raise BridgeError(f"api-bridges request failed for {path}: {exc}") from exc
    if response.status_code >= 400:
        raise BridgeError(f"api-bridges returned {response.status_code} for {path}: {response.text}")
    try:
        return response.json()
    except ValueError as exc:
        raise BridgeError(f"api-bridges returned invalid JSON for {path}") from exc


def _semver_key(value: str) -> tuple[int, int, int] | None:
    match = SEMVER_TAG.fullmatch(value.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _latest_release_tag(payload: Any) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("tags"), list):
        raise BridgeError("api-bridges returned an invalid repository-tags response")
    candidates = [
        (key, str(row.get("name", "")).strip())
        for row in payload["tags"]
        if isinstance(row, dict)
        for key in [_semver_key(str(row.get("name", "")))]
        if key is not None
    ]
    if not candidates:
        raise BridgeError("the repository has no stable SemVer tag")
    return max(candidates, key=lambda item: item[0])[1]


def _jira_issue_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise BridgeError("api-bridges returned an invalid Jira search response")
    rows: list[dict[str, Any]] = []
    for issue in payload["issues"]:
        if not isinstance(issue, dict):
            raise BridgeError("api-bridges returned an invalid Jira issue")
        fields = issue.get("fields")
        if not isinstance(fields, dict):
            raise BridgeError("api-bridges returned a Jira issue without fields")
        key = str(issue.get("key", "")).strip()
        if not key:
            raise BridgeError("api-bridges returned a Jira issue without a key")
        rows.append({"key": key, "fields": fields})
    return rows


async def _ticket_delivery(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    ticket_key: str,
) -> tuple[dict[str, Any] | None, str]:
    try:
        async with semaphore:
            payload = await _bridge_request(
                client,
                "POST",
                "/v1/github/jira-ticket-delivery",
                {
                    "owner": GITHUB_OWNER,
                    "repo": GITHUB_REPO,
                    "ticket_key": ticket_key,
                    "base": GITHUB_BASE,
                },
            )
    except BridgeError as exc:
        logger.warning("GitHub delivery lookup failed for %s: %s", ticket_key, exc)
        return None, "GitHub delivery evidence is temporarily unavailable."
    if not isinstance(payload, dict):
        raise BridgeError(f"api-bridges returned invalid GitHub delivery evidence for {ticket_key}")
    return payload, ""


async def _deployment_overview(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    deployment: dict[str, Any],
) -> DeploymentView:
    env = str(deployment.get("env", "")).strip()
    label = str(deployment.get("display_name") or env).strip()
    if not env:
        raise BridgeError("api-bridges returned a deployment without an environment name")
    try:
        async with semaphore:
            payload = await _bridge_request(
                client,
                "POST",
                "/v1/palliativa/infra-control/overview",
                {"deployment": env},
            )
    except BridgeError as exc:
        logger.warning("Deployment overview failed for %s: %s", env, exc)
        return DeploymentView(
            env=env,
            label=label,
            app_url="",
            deploy_kind=None,
            image_tag="",
            healthy=False,
            error="Deployment details are temporarily unavailable.",
        )
    overview = payload.get("overview") if isinstance(payload, dict) else None
    if not isinstance(overview, dict):
        raise BridgeError(f"api-bridges returned an invalid deployment overview for {env}")
    deployment_info = overview.get("deployment") or {}
    health = overview.get("health") or {}
    routes = overview.get("routes") or {}
    images = overview.get("images") or {}
    deploy_kind = str(deployment_info.get("deploy_kind", ""))
    if deploy_kind not in {"image", "mounted"}:
        raise BridgeError(f"api-bridges returned an invalid deployment kind for {env}")
    backend_tag = str(images.get("backend_effective") or images.get("backend_tag") or "").strip()
    frontend_tag = str(images.get("frontend_effective") or images.get("frontend_tag") or "").strip()
    image_tag = backend_tag if backend_tag == frontend_tag else ""
    app_host = str(routes.get("app_host") or "").strip()
    return DeploymentView(
        env=env,
        label=label,
        app_url=f"https://{app_host}/" if app_host else "",
        deploy_kind=deploy_kind,
        image_tag=image_tag,
        healthy=bool(health.get("ok")),
    )


def _pull_request_links(delivery: dict[str, Any] | None) -> list[PullRequestLink]:
    if delivery is None:
        return []
    rows = delivery.get("pull_requests")
    if not isinstance(rows, list):
        raise BridgeError("api-bridges returned invalid pull-request delivery evidence")
    links: list[PullRequestLink] = []
    for row in rows:
        if not isinstance(row, dict):
            raise BridgeError("api-bridges returned an invalid pull request")
        links.append(
            PullRequestLink(
                title=str(row.get("title") or "Pull request"),
                url=str(row.get("url") or ""),
                merged=row.get("merged_at") is not None,
            )
        )
    return links


def _ticket_position(
    delivery: dict[str, Any] | None,
    feature_builds: list[str],
) -> TicketPosition:
    if delivery is not None:
        pull_requests = delivery.get("pull_requests") or []
        if any(isinstance(row, dict) and row.get("state") == "open" for row in pull_requests):
            return "pull_request"
        if delivery.get("ticket_in_base") is True:
            return "master"
        if delivery.get("latest_merged_pull_request") is not None:
            return "merged_not_master"
    if feature_builds:
        return "built"
    return "ticket_only"


@app.get(
    "/health",
    summary="Check Delivery Map Health",
    description="Return process health for the read-only Palliativa delivery-map API.",
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/delivery-stack",
    response_model=DeliveryStackResponse,
    summary="Get Palliativa Delivery Stack",
    description=(
        "Return the latest stable release, current master distance, Jira ticket delivery evidence, feature builds, "
        "pull requests, and exact matching deployment images through api-bridges."
    ),
)
async def delivery_stack() -> DeliveryStackResponse:
    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            tags_payload, jira_payload, deployments_payload, pull_requests_payload = await asyncio.gather(
                _bridge_request(
                    client,
                    "POST",
                    "/v1/github/repo-tags",
                    {"owner": GITHUB_OWNER, "repo": GITHUB_REPO},
                ),
                _bridge_request(
                    client,
                    "POST",
                    "/v1/jira/search",
                    {
                        "session": JIRA_SESSION,
                        "base_url": JIRA_BASE_URL,
                        "jql": (
                            f"project = {JIRA_PROJECT} AND "
                            "(statusCategory != Done OR fixVersion in unreleasedVersions()) "
                            "ORDER BY updated DESC"
                        ),
                        "max_results": 200,
                        "fields": ["summary", "status", "fixVersions", FEATURE_BUILDS_FIELD],
                    },
                ),
                _bridge_request(client, "GET", "/v1/palliativa/deployments"),
                _bridge_request(
                    client,
                    "POST",
                    "/v1/github/pr-queue",
                    {
                        "owner": GITHUB_OWNER,
                        "repo": GITHUB_REPO,
                        "base": GITHUB_BASE,
                        "state": "all",
                        "max_results": 500,
                    },
                ),
            )
            release_tag = _latest_release_tag(tags_payload)
            compare_payload = await _bridge_request(
                client,
                "POST",
                "/v1/github/compare",
                {
                    "owner": GITHUB_OWNER,
                    "repo": GITHUB_REPO,
                    "base": release_tag,
                    "head": GITHUB_BASE,
                },
            )
            issue_rows = _jira_issue_rows(jira_payload)
            pull_request_rows = (
                pull_requests_payload.get("pulls")
                if isinstance(pull_requests_payload, dict)
                else None
            )
            if not isinstance(pull_request_rows, list):
                raise BridgeError("api-bridges returned an invalid pull-request list")
            ticket_keys_with_pull_requests = {
                issue["key"]
                for issue in issue_rows
                if any(
                    isinstance(pull_request, dict)
                    and str(pull_request.get("title") or "").startswith(f"{issue['key']}:")
                    for pull_request in pull_request_rows
                )
            }
            delivery_semaphore = asyncio.Semaphore(12)
            delivery_by_ticket = dict(
                zip(
                    sorted(ticket_keys_with_pull_requests),
                    await asyncio.gather(
                        *(
                            _ticket_delivery(client, delivery_semaphore, ticket_key)
                            for ticket_key in sorted(ticket_keys_with_pull_requests)
                        )
                    ),
                    strict=True,
                )
            )
            if not isinstance(deployments_payload, dict) or not isinstance(
                deployments_payload.get("deployments"), list
            ):
                raise BridgeError("api-bridges returned an invalid deployment list")
            enabled_deployments = [
                row
                for row in deployments_payload["deployments"]
                if isinstance(row, dict) and row.get("enabled") is True
            ]
            deployment_semaphore = asyncio.Semaphore(8)
            deployments = list(
                await asyncio.gather(
                    *(
                        _deployment_overview(client, deployment_semaphore, row)
                        for row in enabled_deployments
                    )
                )
            )
    except BridgeError as exc:
        logger.error("Delivery stack could not be assembled: %s", exc)
        raise HTTPException(status_code=502, detail="The delivery map could not be assembled from api-bridges.") from exc

    deployment_by_tag: dict[str, list[str]] = {}
    for deployment in deployments:
        if deployment.image_tag:
            deployment_by_tag.setdefault(deployment.image_tag, []).append(deployment.label)

    tickets: list[TicketView] = []
    tickets_in_master = 0
    for issue in issue_rows:
        delivery, delivery_error = delivery_by_ticket.get(issue["key"], (None, ""))
        fields = issue["fields"]
        status = fields.get("status") or {}
        feature_builds = [
            str(value)
            for value in (fields.get(FEATURE_BUILDS_FIELD) or [])
            if str(value).strip()
        ]
        matching_deployments = sorted(
            {
                deployment
                for build in feature_builds
                for deployment in deployment_by_tag.get(build, [])
            }
        )
        position = _ticket_position(delivery, feature_builds)
        if position == "master":
            tickets_in_master += 1
        tickets.append(
            TicketView(
                key=issue["key"],
                summary=str(fields.get("summary") or ""),
                url=f"{JIRA_BASE_URL}/browse/{issue['key']}",
                status=str(status.get("name") or ""),
                fix_versions=[
                    str(version.get("name"))
                    for version in (fields.get("fixVersions") or [])
                    if isinstance(version, dict) and version.get("name")
                ],
                position=position,
                pull_requests=_pull_request_links(delivery),
                feature_builds=feature_builds,
                deployments=matching_deployments,
                delivery_error=delivery_error,
            )
        )

    position_order = {
        "pull_request": 0,
        "built": 1,
        "master": 2,
        "merged_not_master": 3,
        "ticket_only": 4,
    }
    tickets.sort(key=lambda ticket: (position_order[ticket.position], ticket.key))
    commits_since_release = compare_payload.get("ahead_by") if isinstance(compare_payload, dict) else None
    if not isinstance(commits_since_release, int) or commits_since_release < 0:
        raise HTTPException(status_code=502, detail="GitHub comparison did not return a valid commit count.")

    return DeliveryStackResponse(
        generated_at=datetime.now(timezone.utc),
        release=ReleaseLayer(
            version=release_tag,
            url=f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/tree/{release_tag}",
        ),
        master=MasterLayer(
            url=f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/tree/{GITHUB_BASE}",
            commits_since_release=commits_since_release,
            tickets_in_master=tickets_in_master,
        ),
        deployments=sorted(deployments, key=lambda deployment: deployment.env),
        tickets=tickets,
    )
