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
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

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


# Deployments (with pipeline fallback)
@app.get("/deployments")
async def deployments():
    """Latest deployment (or pipeline) per environment for configured repos."""
    email, token = _bitbucket_auth()

    repos_env = os.getenv("BITBUCKET_REPOS", "palliativa/frontend,palliativa/backend")
    repos = [r.strip() for r in repos_env.split(",") if r.strip()]

    out: List[Dict[str, Any]] = []

    async with httpx.AsyncClient() as client:

        def _commit_hash_from_deployment(item: Dict[str, Any]) -> str | None:
            """Best-effort extraction of commit hash from a deployment/pipeline object."""

            paths = [
                ["commit", "hash"],
                ["commit"],  # sometimes commit is directly a string
                ["deployment", "commit", "hash"],
                ["deployment", "commit"],
                ["pull_request", "merge_commit", "hash"],
                ["target", "commit", "hash"],  # pipelines structure
            ]
            for p in paths:
                node = item
                for k in p:
                    if not isinstance(node, dict):
                        node = None
                        break
                    node = node.get(k)
                if node and isinstance(node, str):
                    return node

            # Fallback: Bitbucket deployments may embed commit hash under
            # "revision" or "source.commit.hash" in some older payloads.
            alt = item.get("revision") or item.get("source", {}).get("commit", {}).get("hash")
            if isinstance(alt, str):
                return alt

            # Extract from links.commit.href (…/commit/<hash>)
            href = (
                item.get("links", {}).get("commit", {}) or item.get("links", {}).get("html", {})
            ).get("href")
            if href and "/commit/" in href:
                return href.rstrip("/").split("/commit/")[-1]

            return None

        for repo_full in repos:
            if "/" in repo_full:
                workspace, slug = repo_full.split("/", 1)
                workspace_candidates = [workspace]
            else:
                slug = repo_full
                workspace_candidates = discovered_workspaces or [""]

            success_any_workspace = False
            latest_by_env: Dict[str, Any] = {}

            env_cache: Dict[str, str] = {}


            for workspace in workspace_candidates:
                if not workspace:
                    continue

                # 1. try deployments API
                url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/deployments/"
                print(f"[deploy] GET {url}")
                resp = await client.get(url, auth=(email, token), params={"pagelen": 20})
                print(f"[deploy] → status {resp.status_code} – {len(resp.json().get('values', [])) if resp.status_code == 200 else 'n/a'} items")
                latest_by_env: Dict[str, Any] = {}
                if resp.status_code == 200:
                    success_any_workspace = True
                    for d in resp.json().get("values", []):
                        env_obj = d.get("environment")
                        env_name = None
                        if isinstance(env_obj, dict):
                            env_name = env_obj.get("name") or env_obj.get("environment_type")
                        elif isinstance(env_obj, str):
                            env_name = env_obj

                        print(f"[deploy]   env_raw={env_obj!r} ⇒ env_name={env_name}")

                        if not env_name and isinstance(env_obj, dict):
                            env_uuid = env_obj.get("uuid")
                            if env_uuid and env_uuid not in env_cache:
                                env_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/deployments_config/environments/{env_uuid.strip('{}')}"
                                print(f"[deploy]   GET {env_url} (resolve env uuid)")
                                env_resp = await client.get(env_url, auth=(email, token))
                                if env_resp.status_code == 200:
                                    env_cache[env_uuid] = env_resp.json().get("name") or env_resp.json().get("environment_type")
                                    print(f"[deploy]   → env_name={env_cache[env_uuid]}")
                                else:
                                    env_cache[env_uuid] = None

                            env_name = env_cache.get(env_uuid)

                        if not env_name:
                            continue

                        ts = (
                            d.get("update_time")
                            or d.get("updated_on")
                            or d.get("completed_on")
                            or d.get("created_on")
                            or ""
                        )
                        if env_name not in latest_by_env or ts > (latest_by_env[env_name].get("update_time") or ""):
                            latest_by_env[env_name] = d

                        # Dump raw deployment JSON for debugging (truncated to 1 kB)
                        import json as _json

                        raw_json = _json.dumps(d, default=str)
                        print(
                            f"[deploy]   RAW deployment {env_name}: " + raw_json[:4000]
                        )

                # fallback to pipelines if no deployments
                if not latest_by_env:
                    pipe_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/pipelines/"
                    print(f"[deploy] GET {pipe_url}")
                    resp_p = await client.get(
                        pipe_url,
                        auth=(email, token),
                        params={"pagelen": 10, "sort": "-created_on"},
                    )
                    print(f"[deploy] → status {resp_p.status_code} – {len(resp_p.json().get('values', [])) if resp_p.status_code == 200 else 'n/a'} items")
                    if resp_p.status_code == 200:
                        success_any_workspace = True
                        for p in resp_p.json().get("values", []):
                            state = p.get("state", {})
                            result = state.get("result", {}).get("name") or state.get("name")

                            # Skip pipelines that are still running/cancelled etc.
                            if result in {"IN_PROGRESS", "PENDING", "BUILDING"}:
                                continue

                            env_key = p.get("target", {}).get("ref_name", "pipeline")
                            latest_by_env[env_key] = {
                                "name": p.get("name") or p.get("build_number"),
                                "commit": p.get("target", {}).get("commit", {}).get("hash"),
                                "update_time": p.get("completed_on") or p.get("created_on"),
                                "state": {"result": result},
                                "links": p.get("links"),
                            }

                            break

                # If we found something for this workspace or received 403/404 try next? Break if found
                if latest_by_env:
                    break

            # If no workspace produced data, skip
            if not latest_by_env and not success_any_workspace:
                continue

            # ------------------------------------------------------------
            # Fetch commit details & tag for the selected deployments
            # ------------------------------------------------------------

            commit_cache: Dict[str, Dict[str, Any]] = {}


            async def _enrich_commit(commit_hash: str):
                if commit_hash in commit_cache:
                    return

                commit_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/commit/{commit_hash}"
                resp_c = await client.get(commit_url, auth=(email, token))
                message = date_str = None
                if resp_c.status_code == 200:
                    j = resp_c.json()
                    message = j.get("message")
                    date_str = j.get("date") or j.get("author", {}).get("date")

                # tag lookup
                tags_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/refs/tags"
                resp_t = await client.get(tags_url, auth=(email, token), params={"q": f'target.hash="{commit_hash}"', "pagelen": 1})
                tag = None
                if resp_t.status_code == 200:
                    tags_vals = resp_t.json().get("values", [])
                    if tags_vals:
                        tag = tags_vals[0].get("name")

                commit_cache[commit_hash] = {"message": message, "date": date_str, "tag": tag}

            # collect awaitables
            to_enrich: List[str] = []
            for d in latest_by_env.values():
                ch = _commit_hash_from_deployment(d)
                if ch:
                    to_enrich.append(ch)
                else:
                    print(
                        f"[deploy]   WARNING: no commit hash found in deployment {d.get('uuid') or d.get('name')}. Attempt detail fetch."
                    )

                    dep_uuid = (
                        d.get("uuid")
                        or (d.get("deployment") or {}).get("uuid")
                        or None
                    )
                    if dep_uuid:
                        detail_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/deployments/{dep_uuid.strip('{}')}"
                        print(f"[deploy]   GET {detail_url} (detail)")
                        dep_resp = await client.get(detail_url, auth=(email, token))
                        if dep_resp.status_code == 200:
                            dep_full = dep_resp.json()
                            # merge into original dict for later field lookups
                            d.update(dep_full)
                            ch2 = _commit_hash_from_deployment(dep_full)
                            if ch2 and ch2 not in to_enrich:
                                print(f"[deploy]   → commit resolved {ch2[:7]}")
                                to_enrich.append(ch2)
                                d["commit"] = {"hash": ch2}
                        else:
                            print(f"[deploy]   → detail fetch failed status {dep_resp.status_code}")

            # fetch commit info sequentially (few commits) – keeps code simple
            for ch in to_enrich:
                await _enrich_commit(ch)
                print(f"[deploy]   commit {ch[:7]} enriched: tag={commit_cache[ch].get('tag')}")

            for env, d in latest_by_env.items():
                # Deployment objects may have a "state" dict (subfields differ
                # from pipelines). Pipelines have state.result.name etc. We try
                # several fallbacks and default to "UNKNOWN".
                state_obj = d.get("state") or d.get("deployment_state") or {}
                result = (
                    state_obj.get("result")
                    or (state_obj.get("name") if isinstance(state_obj.get("name"), str) else None)
                    or state_obj.get("status")
                    or "UNKNOWN"
                )

                commit_hash = _commit_hash_from_deployment(d)
                info = commit_cache.get(commit_hash, {}) if commit_hash else {}

                out.append(
                    {
                        "repository": slug,
                        "environment": env,
                        "name": d.get("name"),
                        "commit": commit_hash,
                        "tag": info.get("tag"),
                        "update_time": d.get("update_time")
                        or d.get("updated_on")
                        or d.get("completed_on")
                        or d.get("created_on"),
                        "result": result,
                        "link": d.get("links", {}).get("html", {}).get("href"),
                    }
                )

                # log summary line for each env we captured
                summary_commit = (commit_hash or "")[:7]
                print(
                    f"[deploy] {workspace}/{slug} env={env} result={result} commit={summary_commit} tag={info.get('tag') or '-'}"
                )

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
async def repo_list():
    """Return list of repository slugs configured via BITBUCKET_REPOS env var.

    The response structure is a list of objects with:

        workspace – workspace/owner part (may be empty if not provided)
        slug      – repository slug (name)
        full      – original "workspace/slug" string
        link      – public Bitbucket URL if workspace was included
    """

    # Re-use the same default list as the /deployments endpoint so that the UI
    # always shows at least those example repositories unless overridden via
    # BITBUCKET_REPOS.
    repos_env = os.getenv("BITBUCKET_REPOS", "palliativa/frontend,palliativa/backend")
    repos_raw = [r.strip() for r in repos_env.split(",") if r.strip()]

    out: List[Dict[str, str]] = []
    for repo in repos_raw:
        if "/" in repo:
            workspace, slug = repo.split("/", 1)
            link = f"https://bitbucket.org/{workspace}/{slug}"
        else:
            workspace = ""
            slug = repo
            link = ""
        out.append({"workspace": workspace, "slug": slug, "full": repo, "link": link})

    return out
