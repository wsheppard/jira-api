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

# ---------------------------------------------------------------------------
# Jira configuration
# ---------------------------------------------------------------------------


load_dotenv()

instances_env = os.getenv("JIRA_INSTANCES")
configs: List[Dict[str, Any]] = []
if instances_env:
    for inst in [i.strip() for i in instances_env.split(",") if i.strip()]:
        prefix = inst.upper()
        token = os.getenv(f"{prefix}_JIRA_API_TOKEN")
        email = os.getenv(f"{prefix}_JIRA_EMAIL")
        base_url = os.getenv(f"{prefix}_JIRA_BASE_URL")
        assignees_env = os.getenv(f"{prefix}_JIRA_ASSIGNEES")
        assignees = [a.strip() for a in assignees_env.split(",")] if assignees_env else [email]

        missing = [v for v, val in [
            (f"{prefix}_JIRA_API_TOKEN", token),
            (f"{prefix}_JIRA_EMAIL", email),
            (f"{prefix}_JIRA_BASE_URL", base_url),
        ] if not val]
        if missing:
            raise RuntimeError(f"Missing env vars for {inst}: {', '.join(missing)}")

        configs.append(
            {
                "name": inst,
                "email": email,
                "token": token,
                "base_url": base_url,
                "assignees": assignees,
            }
        )
else:
    # single-instance fallback
    token = os.getenv("JIRA_API_TOKEN")
    email = os.getenv("JIRA_EMAIL")
    base_url = os.getenv("JIRA_BASE_URL")
    for var, val in [("JIRA_API_TOKEN", token), ("JIRA_EMAIL", email), ("JIRA_BASE_URL", base_url)]:
        if not val:
            raise RuntimeError(f"{var} environment variable is not set")
    configs.append({"name": "default", "email": email, "token": token, "base_url": base_url})

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
            resp = await client.get(search_url, auth=(cfg["email"], cfg["token"]), headers=headers, params={"jql": jql})

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"{cfg['name']}: {resp.text}")

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
            resp = await client.get(url, auth=(cfg["email"], cfg["token"]), headers=headers, params={"jql": jql})
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"{cfg['name']}: {resp.text}")

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


_bb_checked = False


def _bitbucket_auth() -> tuple[str, str]:
    """Return (email, token) for Bitbucket API; logs connectivity once."""
    global _bb_checked

    email = os.getenv("BITBUCKET_EMAIL") or os.getenv("JIRA_EMAIL")
    token = os.getenv("BITBUCKET_API_TOKEN") or os.getenv("JIRA_API_TOKEN")
    if not email or not token:
        raise HTTPException(status_code=500, detail="Bitbucket credentials missing: BITBUCKET_EMAIL/API_TOKEN or JIRA_EMAIL/API_TOKEN")

    if not _bb_checked:
        async def _check():
            url = "https://api.bitbucket.org/2.0/user"
            async with httpx.AsyncClient() as client:
                r = await client.get(url, auth=(email, token))
            if r.status_code == 200:
                info = r.json()
                print(f"[bitbucket] Auth OK – user: {info.get('username')} / {info.get('display_name')}")
            else:
                print(f"[bitbucket] Auth FAILED – {r.status_code}: {r.text[:120]}")

        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_check())
        except RuntimeError:
            asyncio.run(_check())

        _bb_checked = True

    return email, token


# ---------------------------------------------------------------------------
# Bitbucket endpoints
# ---------------------------------------------------------------------------


@app.get("/bitbucket-test")
async def bitbucket_test():
    """Return minimal user info to confirm Bitbucket credentials work."""
    email, token = _bitbucket_auth()
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.bitbucket.org/2.0/user", auth=(email, token))
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    data = resp.json()
    return {"username": data.get("username"), "display_name": data.get("display_name")}


@app.get("/bitbucket-commits")
async def bitbucket_commits(workspace: str, repo: str, limit: int = 10):
    email, token = _bitbucket_auth()
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/commits"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, auth=(email, token), params={"pagelen": limit})
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


async def fetch_deployments(client: httpx.AsyncClient, auth: Tuple[str, str], workspace: str, slug: str) -> List[Dict[str, Any]]:
    """
    Fetch deployments for the given repository.
    """
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/deployments/"
    resp = await client.get(url, auth=auth, params={"pagelen": 20})
    if resp.status_code != 200:
        return []
    data = resp.json()
    ret = data.get("values", [])
    return ret


async def fetch_environments(client: httpx.AsyncClient, auth: Tuple[str, str], workspace: str, slug: str) -> List[Dict[str, Any]]:
    """
    Fetch environments for the given repository, including last_deployment for each environment.
    """
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/deployments/environments"
    resp = await client.get(url, auth=auth, params={"pagelen": 50})
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data.get("values", [])

def select_latest_by_env(deployments: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    From a list of deployments, select the latest deployment per environment.
    """
    latest: Dict[str, Dict[str, Any]] = {}
    for d in deployments:
        env_obj = d.get("environment")
        env_name: str | None = None
        if isinstance(env_obj, dict):
            env_name = env_obj.get("name") or env_obj.get("environment_type")
        elif isinstance(env_obj, str):
            env_name = env_obj
        if not env_name:
            continue
        ts = d.get("update_time") or d.get("updated_on") or d.get("completed_on") or d.get("created_on") or ""
        if env_name not in latest or ts > (latest[env_name].get("update_time") or ""):
            latest[env_name] = d
    return latest

def _commit_hash_from_deployment(item: Dict[str, Any]) -> str | None:
    """Best-effort extraction of commit hash from a deployment object."""
    href = (item.get("links", {}).get("commit", {}) or item.get("links", {}).get("html", {})).get("href")
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
            message = data.get("message")
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
async def deployments() -> list[Dict[str, Any]]:
    """
    Latest deployment per environment for configured repos.
    """
    email, token = _bitbucket_auth()
    repos_env = os.getenv("BITBUCKET_REPOS", "palliativa/frontend,palliativa/backend")
    repos = [r.strip() for r in repos_env.split(",") if r.strip()]
    out: List[Dict[str, Any]] = []

    auth = (email, token)
    async with httpx.AsyncClient() as client:
        for repo_full in repos:
            if "/" not in repo_full:
                continue
            workspace, slug = repo_full.split("/", 1)
            envs = await fetch_environments(client, auth, workspace, slug)
            if not envs:
                continue

            commit_hashes: list[str] = []
            for env in envs:
                ld = env.get("last_deployment")
                if not ld:
                    continue
                ch = _commit_hash_from_deployment(ld)
                if ch:
                    commit_hashes.append(ch)
            commit_cache = await enrich_commits(client, auth, workspace, slug, commit_hashes)

            for env in envs:
                ld = env.get("last_deployment")
                if not ld:
                    continue
                commit_hash = _commit_hash_from_deployment(ld)
                info = commit_cache.get(commit_hash, {}) if commit_hash else {}
                env_name = env.get("name") or env.get("environment_type") or ""
                out.append({
                    "repository": slug,
                    "environment": env_name,
                    "name": ld.get("name") or ld.get("uuid") or env_name,
                    "commit": commit_hash,
                    "tag": info.get("tag"),
                    "update_time": ld.get("update_time") or ld.get("updated_on") or ld.get("completed_on") or ld.get("created_on"),
                    "result": (ld.get("state") or ld.get("deployment_state") or {}).get("result") or None,
                    "link": ld.get("links", {}).get("html", {}).get("href"),
                })

    out.sort(key=lambda x: (x["repository"], x["environment"]))
    return out

# Repo list
@app.get("/bitbucket-repos")
async def bitbucket_repos(workspace: str):
    """List repos in a Bitbucket workspace."""
    email, token = _bitbucket_auth()
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}"
    repos: List[Dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        next_url = url
        while next_url:
            resp = await client.get(next_url, auth=(email, token), params={"pagelen": 50})
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
    email, token = _bitbucket_auth()
    repos_env = os.getenv("BITBUCKET_REPOS", "palliativa/frontend,palliativa/backend")
    repos_raw = [r.strip() for r in repos_env.split(",") if r.strip()]

    out: List[Dict[str, Any]] = []
    auth = (email, token)
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
