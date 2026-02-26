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

import asyncio
import copy
import logging
import os
import re
import time
from datetime import date
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pipeline_dashboard import PipelineDashboard

JIRA_KEY_REGEX = re.compile(r"\b((?:AP|PD)-\d+)\b")

# reduce noisy dependency logging; disable httpx chatter entirely
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("httpcore").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# module logger
logger = logging.getLogger(__name__)

print("--- JIRA-API: Application starting up ---")

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
github_token = os.getenv("GITHUB_TOKEN")
if not github_token:
    raise RuntimeError("GITHUB_TOKEN environment variable must be set")

# Hard-code Jira instances
configs: List[Dict[str, Any]] = [
    {"name": "palliativa", "email": email, "token": jira_token, "base_url": "https://palliativa.atlassian.net"},
    {"name": "jjrsoftware", "email": email, "token": jira_token, "base_url": "https://jjrsoftware.atlassian.net"},
]

# Cache Jira results to avoid thrashing the API; configure TTL via env var.
JIRA_CACHE_TTL_SECONDS = int(os.getenv("JIRA_CACHE_TTL_SECONDS", "20"))
_jira_cache: Dict[Tuple[str, Tuple[str, ...]], Tuple[float, List[Dict[str, Any]]]] = {}
_jira_cache_lock = asyncio.Lock()


def _adf_to_text(node: Any) -> str:
    """Best-effort conversion of Atlassian Document Format to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(child) for child in node)
    if isinstance(node, dict):
        node_type = node.get("type")
        # Text node
        if node_type == "text":
            return node.get("text", "")
        # Hard/soft breaks
        if node_type in {"hardBreak", "paragraph", "heading", "bulletList", "orderedList", "listItem"}:
            content = node.get("content") or []
            separator = "\n" if node_type in {"paragraph", "heading", "listItem"} else ""
            return separator.join(filter(None, (_adf_to_text(c) for c in content)))
        # If it's a container with content
        content = node.get("content")
        if content:
            return "".join(_adf_to_text(c) for c in content)
    return ""


def _latest_comment(issue_fields: Dict[str, Any]) -> Dict[str, Any] | None:
    comments_block = issue_fields.get("comment") or {}
    comments = comments_block.get("comments") or []
    if not comments:
        return None
    latest = max(comments, key=lambda c: c.get("updated") or c.get("created") or "")
    body_raw = latest.get("body")
    body_text = _adf_to_text(body_raw).strip()
    if len(body_text) > 300:
        body_text = body_text[:297] + "..."
    return {
        "author": (latest.get("author") or {}).get("displayName"),
        "created": latest.get("created") or latest.get("updated"),
        "body": body_text,
    }

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


app = FastAPI(title="Jira / Bitbucket Bridge")


# Add CORS middleware to allow requests from the frontend service
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Jira endpoints
# ---------------------------------------------------------------------------


async def _search_jira(jql: str, fields: List[str]) -> List[Dict[str, Any]]:
    """Run a JQL search across all configured Jira instances and return a
    flattened list of issues.
    """
    cache_key = (jql, tuple(fields))
    if JIRA_CACHE_TTL_SECONDS > 0:
        now = time.monotonic()
        async with _jira_cache_lock:
            cached = _jira_cache.get(cache_key)
            if cached:
                cached_at, cached_payload = cached
                if now - cached_at < JIRA_CACHE_TTL_SECONDS:
                    logger.info("Serving cached Jira response for JQL: %s", jql)
                    return copy.deepcopy(cached_payload)
                _jira_cache.pop(cache_key, None)

    flattened: List[Dict[str, Any]] = []
    headers = {"Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:
        for cfg in configs:
            json_data = {"jql": jql, "fields": fields}
            search_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search/jql"
            logger.info("Querying Jira [%s]: %s", cfg["name"], jql)
            resp = await client.post(search_url, auth=(cfg["email"], cfg["token"]),
                                     headers=headers, json=json_data)

            if resp.status_code != 200:
                print(f"--- JIRA ERROR ---")
                print(f"Response body:\n{resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

            for issue in resp.json().get("issues", []):
                issue_fields = issue.get("fields", {})
                assignee = issue_fields.get("assignee") or {}
                avatar = (assignee.get("avatarUrls") or {}).get("32x32")
                priority = (issue_fields.get("priority") or {}).get("name")
                issuetype = (issue_fields.get("issuetype") or {}).get("name")

                latest_comment = _latest_comment(issue_fields)
                status = issue_fields.get("status") or {}
                status_category = (status.get("statusCategory") or {}).get("name")

                flattened.append(
                    {
                        "instance": cfg["name"],
                        "ticket": issue.get("key"),
                        "project": issue_fields.get("project", {}).get("key"),
                        "assignee": assignee.get("displayName"),
                        "avatarUrl": avatar,
                        "updated": issue_fields.get("updated"),
                        "dueDate": issue_fields.get("duedate"),
                        "title": issue_fields.get("summary"),
                        "link": f"{cfg['base_url'].rstrip('/')}/browse/{issue.get('key')}",
                        "priority": priority,
                        "labels": issue_fields.get("labels", []),
                        "issuetype": issuetype,
                        "statusName": status.get("name"),
                        "statusCategory": status_category,
                        "latestComment": latest_comment,
                    }
                )
    if JIRA_CACHE_TTL_SECONDS > 0:
        async with _jira_cache_lock:
            _jira_cache[cache_key] = (time.monotonic(), copy.deepcopy(flattened))
    return flattened


async def fetch_jira_statuses(keys: List[str]) -> Dict[str, Dict[str, str]]:
    """Fetch Jira status and link for AP/PD issues in the palliativa instance."""
    if not keys:
        return {}
    unique_keys = sorted(set(keys))
    fields = ["status", "summary", "key"]
    jql = f"key in ({', '.join(unique_keys)})"
    headers = {"Content-Type": "application/json"}
    cfg = next((item for item in configs if item.get("name") == "palliativa"), None)
    if not cfg:
        raise RuntimeError("Palliativa Jira config not found")
    async with httpx.AsyncClient() as client:
        search_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search/jql"
        resp = await client.post(search_url, auth=(cfg["email"], cfg["token"]), headers=headers, json={"jql": jql, "fields": fields})
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    results: Dict[str, Dict[str, str]] = {}
    for issue in resp.json().get("issues", []):
        key = issue.get("key")
        fields_data = issue.get("fields", {}) or {}
        status = (fields_data.get("status") or {}).get("name")
        summary = fields_data.get("summary") or ""
        if key:
            results[key] = {
                "key": key,
                "status": status or "",
                "summary": summary,
                "link": f"{cfg['base_url'].rstrip('/')}/browse/{key}",
            }
    return results


async def fetch_pr_details(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    owner: str,
    repo: str,
    number: int,
) -> Dict[str, str] | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
    resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    return {
        "number": data.get("number"),
        "title": data.get("title") or "",
        "link": data.get("html_url") or "",
    }


async def fetch_pr_commits(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    owner: str,
    repo: str,
    number: int,
) -> List[Dict[str, str]]:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/commits"
    resp = await client.get(url, headers=headers, params={"per_page": 100})
    if resp.status_code != 200:
        return []
    commits = []
    for item in resp.json():
        commit_info = item.get("commit") or {}
        author_info = commit_info.get("author") or {}
        raw_message = commit_info.get("message") or ""
        commits.append(
            {
                "sha": item.get("sha"),
                "message": raw_message.split("\n")[0],
                "author": author_info.get("name"),
                "date": author_info.get("date"),
                "link": item.get("html_url"),
            }
        )
    return commits


@app.get("/in-progress")
async def in_progress():
    """Aggregate Jira issues with status *In Progress* across instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'statusCategory = "In Progress"'
    flattened = await _search_jira(jql, fields)
    flattened.sort(key=lambda i: i.get("updated") or "")
    return flattened


@app.get("/open-issues-by-due")
async def open_issues_by_due():
    """Open (not-done) Jira issues sorted by due date (overdue first)."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "issuetype"]
    jql = "statusCategory != Done AND duedate IS NOT EMPTY"
    aggregated = await _search_jira(jql, fields)

    today = date.today().isoformat()

    # Only show issues with an explicit due date; the frontend "due" view
    # should not list everything that's merely open.
    aggregated = [item for item in aggregated if item.get("dueDate")]

    def sort_key(item: Dict[str, Any]):
        due = item.get("dueDate")
        return (due >= today, due)  # overdue first (False < True)

    aggregated.sort(key=sort_key)
    return aggregated


@app.get("/backlog")
async def backlog():
    """Aggregate Jira issues with status *To Do* from Palliativa instance."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "issuetype"]
    jql = 'project = "AP" AND statusCategory = "To Do" ORDER BY updated ASC'
    flattened = await _search_jira(jql, fields)
    return flattened


@app.get("/manager-meeting")
async def manager_meeting():
    """Tickets tagged for the manager meeting across Jira instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'statusCategory != Done AND labels = "ManagerMeeting"'
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "")
    return tickets


@app.get("/recently-updated")
async def recently_updated():
    """Tickets updated in the last 72h but not within the last 30 minutes."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype", "comment"]
    jql = "updated >= -72h AND updated <= -30m"
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "", reverse=True)
    return tickets


@app.get("/codex-enrich")
async def codex_enrich():
    """Tickets tagged for Codex enrichment across Jira instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'labels in ("codex:enrich", "codex:enriched")'
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "", reverse=True)
    return tickets


@app.get("/codex-more-info")
async def codex_more_info():
    """Tickets tagged for additional Codex info across Jira instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'labels = "codex:more-info"'
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "", reverse=True)
    return tickets


@app.get("/codex-implemented")
async def codex_implemented():
    """Tickets tagged as implemented by Codex across Jira instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'labels = "codex:implemented"'
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "", reverse=True)
    return tickets


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


# ---------------------------------------------------------------------------
# GitHub endpoints
# ---------------------------------------------------------------------------


@app.get("/github-branch-commits")
async def github_branch_commits(
    owner: str,
    repo: str,
    base: str = "master",
    head: str = "codex/integration",
) -> Dict[str, Any]:
    """Return commits on codex/integration that are not reachable from base."""
    pr_number_regexes = [
        re.compile(r"merge pull request #(?P<num>\d+)", re.IGNORECASE),
        re.compile(r"\(#(?P<num>\d+)\)"),
    ]

    def extract_pr_numbers(text: str | None) -> List[int]:
        if not text:
            return []
        found: List[int] = []
        for regex in pr_number_regexes:
            for match in regex.finditer(text):
                try:
                    found.append(int(match.group("num")))
                except (TypeError, ValueError):
                    continue
        seen: set[int] = set()
        ordered: List[int] = []
        for num in found:
            if num not in seen:
                ordered.append(num)
                seen.add(num)
        return ordered

    def is_merge_commit(commit_payload: Dict[str, Any]) -> bool:
        parents = commit_payload.get("parents") or []
        return len(parents) > 1

    def extract_jira_keys(text: str | None) -> List[str]:
        if not text:
            return []
        matches = JIRA_KEY_REGEX.findall(text)
        seen: set[str] = set()
        ordered: List[str] = []
        for key in matches:
            if key not in seen:
                ordered.append(key)
                seen.add(key)
        return ordered

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    compare_url = f"https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}"
    head_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{head}"
    async with httpx.AsyncClient() as client:
        compare_resp = await client.get(compare_url, headers=headers)
        head_resp = await client.get(head_url, headers=headers)
    if compare_resp.status_code != 200:
        raise HTTPException(status_code=compare_resp.status_code, detail=compare_resp.text)
    if head_resp.status_code != 200:
        raise HTTPException(status_code=head_resp.status_code, detail=head_resp.text)

    data = compare_resp.json()
    head_sha = head_resp.json().get("sha")
    commits_raw = data.get("commits", [])
    commit_shas = [commit.get("sha") for commit in commits_raw if commit.get("sha")]
    if head_sha and head_sha not in commit_shas:
        commit_shas.append(head_sha)

    jira_base_url = ""
    palliativa_cfg = next((item for item in configs if item.get("name") == "palliativa"), None)
    if palliativa_cfg:
        jira_base_url = palliativa_cfg.get("base_url", "").rstrip("/")

    jira_keys: List[str] = []
    for commit in commits_raw:
        raw_message = (commit.get("commit") or {}).get("message") or ""
        jira_keys.extend(extract_jira_keys(raw_message))
    jira_lookup = await fetch_jira_statuses(jira_keys)

    def build_jira_entries(keys: List[str]) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []
        for key in keys:
            entry = jira_lookup.get(key)
            if entry:
                entries.append(entry)
            else:
                link = f"{jira_base_url}/browse/{key}" if jira_base_url else ""
                entries.append({"key": key, "status": "", "link": link})
        return entries

    tags_by_commit: Dict[str, List[str]] = {}
    latest_tag: str | None = None
    if commit_shas:
        tags_url = f"https://api.github.com/repos/{owner}/{repo}/tags"
        remaining = set(commit_shas)
        page = 1
        async with httpx.AsyncClient() as client:
            while remaining or latest_tag is None:
                resp_t = await client.get(tags_url, headers=headers, params={"per_page": 100, "page": page})
                if resp_t.status_code != 200:
                    raise HTTPException(status_code=resp_t.status_code, detail=resp_t.text)
                values = resp_t.json()
                if not values:
                    break
                for tag in values:
                    tag_name = tag.get("name")
                    tag_commit = (tag.get("commit") or {}).get("sha")
                    if not tag_name or not tag_commit:
                        continue
                    tags_by_commit.setdefault(tag_commit, []).append(tag_name)
                    if tag_commit in remaining:
                        remaining.discard(tag_commit)
                    if head_sha and tag_commit == head_sha and latest_tag is None:
                        latest_tag = tag_name
                page += 1

    commit_pr_numbers: Dict[str, List[int]] = {}
    unique_pr_numbers: set[int] = set()
    for commit in commits_raw:
        raw_message = (commit.get("commit") or {}).get("message") or ""
        sha = commit.get("sha")
        if not sha:
            continue
        pr_numbers = extract_pr_numbers(raw_message)
        commit_pr_numbers[sha] = pr_numbers
        unique_pr_numbers.update(pr_numbers)

    async def load_pr_data(
        pr_client: httpx.AsyncClient, pr_number: int
    ) -> Tuple[int, Dict[str, str] | None, List[Dict[str, str]]]:
        pr_detail, pr_commits = await asyncio.gather(
            fetch_pr_details(pr_client, headers, owner, repo, pr_number),
            fetch_pr_commits(pr_client, headers, owner, repo, pr_number),
        )
        return pr_number, pr_detail, pr_commits

    pr_cache: Dict[int, Dict[str, str]] = {}
    pr_commits_cache: Dict[int, List[Dict[str, str]]] = {}
    if unique_pr_numbers:
        async with httpx.AsyncClient() as pr_client:
            pr_results = await asyncio.gather(
                *(load_pr_data(pr_client, pr_number) for pr_number in sorted(unique_pr_numbers))
            )
        for pr_number, pr_detail, pr_commits in pr_results:
            if pr_detail:
                pr_cache[pr_number] = pr_detail
            pr_commits_cache[pr_number] = pr_commits

    commits: List[Dict[str, Any]] = []
    for commit in commits_raw:
        commit_info = commit.get("commit") or {}
        author_info = commit_info.get("author") or {}
        raw_message = commit_info.get("message") or ""
        sha = commit.get("sha")
        jira_entries = build_jira_entries(extract_jira_keys(raw_message))
        pr_numbers = commit_pr_numbers.get(sha or "", [])
        prs: List[Dict[str, str]] = [pr_cache[num] for num in pr_numbers if num in pr_cache]
        nested_commits: List[Dict[str, str]] = []
        if is_merge_commit(commit):
            for num in pr_numbers:
                if num in pr_commits_cache:
                    nested_commits = pr_commits_cache[num]
                    break
        commits.append(
            {
                "sha": sha,
                "date": author_info.get("date"),
                "author": author_info.get("name"),
                "message": raw_message.split("\n")[0],
                "link": commit.get("html_url"),
                "tags": tags_by_commit.get(sha, []),
                "jira": jira_entries,
                "prs": prs,
                "nested_commits": nested_commits,
                "is_merge_commit": is_merge_commit(commit),
            }
        )
    commits.sort(key=lambda item: item.get("date") or "", reverse=True)

    return {
        "owner": owner,
        "repo": repo,
        "base": base,
        "head": head,
        "ahead_by": data.get("ahead_by"),
        "behind_by": data.get("behind_by"),
        "latest_tag": latest_tag,
        "total_commits": len(commits),
        "commits": commits,
    }


@app.get("/staging-tickets")
async def staging_tickets(project: str = "AP") -> List[Dict[str, Any]]:
    """Return release-train / RC / test-execution tickets for staging visibility."""
    cfg = next((item for item in configs if item.get("name") == "palliativa"), None)
    if not cfg:
        raise RuntimeError("Palliativa Jira config not found")

    jql = (
        f'project = "{project}" AND labels in ("release-train", "rc-candidate", "test-execution", "codex-integration") '
        "ORDER BY created DESC"
    )
    fields = ["summary", "status", "labels", "issuetype", "fixVersions", "updated"]
    headers = {"Content-Type": "application/json"}
    search_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search/jql"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            search_url,
            auth=(cfg["email"], cfg["token"]),
            headers=headers,
            json={"jql": jql, "fields": fields},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    results: List[Dict[str, Any]] = []
    for issue in resp.json().get("issues", []):
        fields_data = issue.get("fields", {}) or {}
        results.append(
            {
                "ticket": issue.get("key"),
                "title": fields_data.get("summary") or "",
                "statusName": (fields_data.get("status") or {}).get("name") or "",
                "issuetype": (fields_data.get("issuetype") or {}).get("name") or "",
                "labels": fields_data.get("labels") or [],
                "fixVersions": [(item.get("name") or "") for item in (fields_data.get("fixVersions") or []) if item],
                "updated": fields_data.get("updated"),
                "link": f"{cfg['base_url'].rstrip('/')}/browse/{issue.get('key')}",
            }
        )
    return results


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
    categories = ["dev/*", "qa/v*", "staging/v*", "prod/v*"]
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
    categories = ["dev/*", "qa/v*", "staging/v*", "prod/v*"]
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
