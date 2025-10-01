"""
FastAPI bridge for Jira and Bitbucket Cloud.

Features
--------
* `/`                         – serve static dashboard
* `/in-progress`              – Jira issues with status "In Progress"
* `/open-issues-by-due`       – Jira issues not done/closed, sorted by due-date
* `/bitbucket-test`           – sanity-check Bitbucket credentials
* `/bitbucket-commits`        – list recent commits for a repo
* `/deployments`              – latest deployment (or pipeline) per environment
* `/bitbucket-repos`          – list repos in a workspace

Auth
----
Bitbucket endpoints use HTTP Basic with one of:
  • BITBUCKET_EMAIL  + BITBUCKET_API_TOKEN
  • JIRA_EMAIL       + JIRA_API_TOKEN      (fallback)

Jira endpoints use the per-instance email/token pairs configured through the
existing environment variables.
"""

from __future__ import annotations

import base64
import os
from datetime import date
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import logging
import asyncio
import json
from bbclient import BitbucketClient
from pipeline_dashboard import PipelineDashboard

# enable debug logs for HTTPX calls (requests/responses), suppress socket-level chatter
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# module logger
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jira configuration
# ---------------------------------------------------------------------------


# Load .env and require a single Atlassian API token for all requests
load_dotenv()
# Hard-code Atlassian email and require separate API tokens for Jira and Bitbucket
email = "will@jjrsoftware.co.uk"
jira_token = os.getenv("JIRA_API_TOKEN")
if not jira_token:
    raise RuntimeError("JIRA_API_TOKEN environment variable must be set")
bb_token = os.getenv("BITBUCKET_API_TOKEN")
if not bb_token:
    raise RuntimeError("BITBUCKET_API_TOKEN environment variable must be set")

# Hard-code Jira instances
configs: List[Dict[str, Any]] = [
    {"name": "palliativa", "email": email, "token": jira_token, "base_url": "https://palliativa.atlassian.net"},
    {"name": "jjrsoftware", "email": email, "token": jira_token, "base_url": "https://jjrsoftware.atlassian.net"},
]

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


app = FastAPI(title="Jira / Bitbucket Bridge")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Static UI
@app.get("/", include_in_schema=False)
async def serve_ui():
    return FileResponse("static/index.html")


# ---------------------------------------------------------------------------
# Jira endpoints
# ---------------------------------------------------------------------------


def _jira_client(cfg: Dict[str, Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(auth=(cfg["email"], cfg["token"]))


@app.get("/in-progress")
async def in_progress():
    """Aggregate Jira issues with status *In Progress* across instances."""
    flattened: List[Dict[str, Any]] = []
    headers = {"Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:


        for cfg in configs:
            jql = 'status = "In Progress"'

            search_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search"
            try:
                resp = await client.get(search_url, auth=(cfg["email"], cfg["token"]), headers=headers, params={"jql": jql})
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(f"Jira API request failed for instance '{cfg['name']}' with JQL '{jql}': {e}")
                logger.debug(f"Jira response: {resp.text}")
                # Re-raise as HTTPException to maintain original API behavior
                raise HTTPException(status_code=resp.status_code, detail=f"Jira API error for instance '{cfg['name']}': {resp.text}") from e


            for issue in resp.json().get("issues", []):
                fields = issue.get("fields", {})
                assignee = fields.get("assignee") or {}
                avatar = (assignee.get("avatarUrls") or {}).get("32x32")

                flattened.append(
                    {
                        "instance": cfg["name"],
                        "ticket": issue.get("key"),
                        "project": fields.get("project", {}).get("key"),
                        "assignee": assignee.get("displayName"),
                        "avatarUrl": avatar,
                        "updated": fields.get("updated"),
                        "dueDate": fields.get("duedate"),
                        "title": fields.get("summary"),
                        "link": f"{cfg['base_url'].rstrip('/')}/browse/{issue.get('key')}",
                    }
                )

    flattened.sort(key=lambda i: i.get("updated") or "")
    return flattened


@app.get("/open-issues-by-due")
async def open_issues_by_due():
    """Open (not-done) Jira issues sorted by due date (overdue first)."""
    aggregated: List[Dict[str, Any]] = []
    headers = {"Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:
        for cfg in configs:
            jql = "statusCategory != Done"
            url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search"
            try:
                resp = await client.get(url, auth=(cfg["email"], cfg["token"]), headers=headers, params={"jql": jql})
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(f"Jira API request failed for instance '{cfg['name']}' with JQL '{jql}': {e}")
                logger.debug(f"Jira response: {resp.text}")
                # Re-raise as HTTPException to maintain original API behavior
                raise HTTPException(status_code=resp.status_code, detail=f"Jira API error for instance '{cfg['name']}': {resp.text}") from e


            for issue in resp.json().get("issues", []):
                fields = issue.get("fields", {})
                due = fields.get("duedate")
                if not due:
                    continue  # skip issues without due-date

                assignee = fields.get("assignee") or {}
                avatar = (assignee.get("avatarUrls") or {}).get("32x32")

                aggregated.append(
                    {
                        "instance": cfg["name"],
                        "ticket": issue.get("key"),
                        "project": fields.get("project", {}).get("key"),
                        "assignee": assignee.get("displayName"),
                        "avatarUrl": avatar,
                        "updated": fields.get("updated"),
                        "dueDate": due,
                        "title": fields.get("summary"),
                        "link": f"{cfg['base_url'].rstrip('/')}/browse/{issue.get('key')}",
                    }
                )

    today = date.today().isoformat()

    def sort_key(item: Dict[str, Any]):
        due = item["dueDate"]
        return (due >= today, due)  # overdue first (False < True)

    aggregated.sort(key=sort_key)
    return aggregated


# ---------------------------------------------------------------------------
# Bitbucket helpers
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Bitbucket endpoints
# ---------------------------------------------------------------------------


@app.get("/bitbucket-test")
async def bitbucket_test():
    """Return minimal user info to confirm Bitbucket credentials work."""
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.bitbucket.org/2.0/user", auth=(email, bb_token))
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    data = resp.json()
    return {"username": data.get("username"), "display_name": data.get("display_name")}


@app.get("/bitbucket-commits")
async def bitbucket_commits(workspace: str, repo: str, limit: int = 10):
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/commits"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, auth=(email, bb_token), params={"pagelen": limit})
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    commits = []
    for c in resp.json().get("values", []):
        commits.append(
            {
                "hash": c.get("hash"),
                "date": c.get("date"),
                "message": (c.get("message") or "").split("\n")[0],
                "author": c.get("author", {}).get("raw"),
                "link": c.get("links", {}).get("html", {}).get("href"),
            }
        )
    return commits


async def fetch_all_deployments(
    client: httpx.AsyncClient,
    auth: Tuple[str, str],
    workspace: str,
    slug: str,
) -> List[Dict[str, Any]]:
    """
    Fetch all deployments for the given repository by paging through results.
    """
    base_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/deployments"
    deployments: List[Dict[str, Any]] = []
    next_url: str | None = f"{base_url}?pagelen=50"
    while next_url:
        resp = await client.get(next_url, auth=auth)
        if resp.status_code != 200:
            break
        data = resp.json()
        values = data.get("values", [])
        if not values:
            break
        deployments.extend(values)
        next_url = data.get("next")
    return deployments

async def fetch_deployment_statuses(
    client: httpx.AsyncClient,
    auth: Tuple[str, str],
    workspace: str,
    slug: str,
    deployment_uuid: str,
) -> List[Dict[str, Any]]:
    """
    Fetch statuses for the specified deployment (e.g. to find SUCCESSFUL state).
    """
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/deployments/{deployment_uuid}/statuses"
    resp = await client.get(url, auth=auth, params={"pagelen": 50})
    if resp.status_code != 200:
        return []
    return resp.json().get("values", [])

async def fetch_environments(client: httpx.AsyncClient, auth: Tuple[str, str], workspace: str, slug: str) -> List[Dict[str, Any]]:
    """
    Fetch environments for the given repository.
    """
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/environments"
    resp = await client.get(url, auth=auth, params={"pagelen": 50})
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data.get("values", [])




def _commit_hash_from_deployment(item: Dict[str, Any]) -> str | None:
    """Best-effort extraction of commit hash from a deployment object."""
    # First, try nested commit objects under deployable or release
    dep_commit = (
        item.get("deployable", {})
            .get("commit", {})
            .get("hash")
    )
    if dep_commit:
        return dep_commit
    rel_commit = (
        item.get("release", {})
            .get("commit", {})
            .get("hash")
    )
    if rel_commit:
        return rel_commit
    # Fallback: parse commit href links
    href = (
        (item.get("links", {}).get("commit", {}) or item.get("links", {}).get("html", {}))
        .get("href")
    )
    if href and "/commit/" in href:
        return href.rstrip("/").split("/commit/")[-1]
    return None

async def enrich_commits(client: httpx.AsyncClient, auth: Tuple[str, str], workspace: str, slug: str, commit_hashes: list[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch commit message, date, and tag for each commit hash.
    """
    commit_cache: Dict[str, Dict[str, Any]] = {}
    for commit_hash in commit_hashes:
        # commit details
        url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/commit/{commit_hash}"
        resp = await client.get(url, auth=auth)
        message = None
        date_str = None
        if resp.status_code == 200:
            data = resp.json()
            # show only first line of commit message as name
            raw_msg = data.get("message") or ""
            message = raw_msg.split("\n")[0]
            date_str = data.get("date") or data.get("author", {}).get("date")
        # tag lookup
        tags_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/refs/tags"
        resp_t = await client.get(tags_url, auth=auth, params={"q": f'target.hash="{commit_hash}"', "pagelen": 1})
        tag = None
        if resp_t.status_code == 200:
            vals = resp_t.json().get("values", [])
            if vals:
                tag = vals[0].get("name")
        commit_cache[commit_hash] = {"message": message, "date": date_str, "tag": tag}
    return commit_cache

@app.get("/deployments")
async def deployments() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """
    Alias for the pipeline-dashboard: latest pipeline runs per tag-category per repo.
    """
    repos = ["palliativa/frontend", "palliativa/backend"]
    categories = ["qa/v*", "staging/v*", "prod/v*"]
    dashboard = PipelineDashboard(bb_token, repos)
    return await dashboard.get_dashboard(
        categories=categories,
        pagelen=10,
        max_items=10,
    )


@app.get("/pipeline-dashboard")
async def pipeline_dashboard() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Return the latest pipelines per tag-category per configured repositories."""
    repos = ["palliativa/frontend", "palliativa/backend"]
    categories = ["qa/v*", "staging/v*", "prod/v*"]
    dashboard = PipelineDashboard(bb_token, repos)
    return await dashboard.get_dashboard(categories=categories, pagelen=10, max_items=10)

# Repo list
@app.get("/bitbucket-repos")
async def bitbucket_repos(workspace: str):
    """List repos in a Bitbucket workspace."""
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}"
    repos: List[Dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        next_url = url
        while next_url:
            resp = await client.get(next_url, auth=(email, bb_token), params={"pagelen": 50})
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            data = resp.json()
            for r in data.get("values", []):
                repos.append(
                    {
                        "workspace": workspace,
                        "slug": r.get("slug"),
                        "name": r.get("name"),
                        "is_private": r.get("is_private"),
                        "link": r.get("links", {}).get("html", {}).get("href"),
                    }
                )
            next_url = data.get("next")

    repos.sort(key=lambda r: r["slug"])
    return repos


# ---------------------------------------------------------------------------
# Simple repo list (from env var)
# ---------------------------------------------------------------------------


@app.get("/repos")
async def repo_list() -> List[Dict[str, Any]]:
    """Return list of repository slugs configured via BITBUCKET_REPOS env var,
    along with deployment environments per repo.

    The response structure is a list of objects with:

        workspace     – workspace/owner part (may be empty if not provided)
        slug          – repository slug (name)
        full          – original "workspace/slug" string
        link          – public Bitbucket URL if workspace was included
        environments  – list of environment names for deployments in that repo
    """
    # Hard-code Bitbucket repos to inspect for deployments
    repos_raw = ["palliativa/frontend", "palliativa/backend"]

    out: List[Dict[str, Any]] = []
    auth = (email, bb_token)
    async with httpx.AsyncClient() as client:
        for repo in repos_raw:
            if "/" in repo:
                workspace, slug = repo.split("/", 1)
                link = f"https://bitbucket.org/{workspace}/{slug}"
                envs = await fetch_environments(client, auth, workspace, slug)
                env_names = [
                    e.get("name") or e.get("environment_type") or ""
                    for e in envs
                    if e.get("name") or e.get("environment_type")
                ]
            else:
                workspace = ""
                slug = repo
                link = ""
                env_names: List[str] = []
            out.append(
                {
                    "workspace": workspace,
                    "slug": slug,
                    "full": repo,
                    "link": link,
                    "environments": env_names,
                }
            )
    return out
