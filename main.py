"""
FastAPI service to bridge Jira Cloud API.

Provides a route to retrieve 'In Progress' issues across configured Jira instances.
Returns a flat list of dicts with instance name, ticket key, project key, assignee email,
title summary, and a URL link to the issue.
"""
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

load_dotenv()

instances_env = os.getenv("JIRA_INSTANCES")
configs = []
if instances_env:
    for inst in [i.strip() for i in instances_env.split(",") if i.strip()]:
        prefix = inst.upper()
        token = os.getenv(f"{prefix}_JIRA_API_TOKEN")
        email = os.getenv(f"{prefix}_JIRA_EMAIL")
        base_url = os.getenv(f"{prefix}_JIRA_BASE_URL")
        assignees_env = os.getenv(f"{prefix}_JIRA_ASSIGNEES")
        if assignees_env:
            assignees = [a.strip() for a in assignees_env.split(",") if a.strip()]
        else:
            assignees = [email]
        missing = [v for v, val in [
            (f"{prefix}_JIRA_API_TOKEN", token),
            (f"{prefix}_JIRA_EMAIL", email),
            (f"{prefix}_JIRA_BASE_URL", base_url),
        ] if not val]
        if missing:
            raise RuntimeError(f"Missing env vars for {inst}: {', '.join(missing)}")
        configs.append({
            "name": inst,
            "email": email,
            "token": token,
            "base_url": base_url,
            "assignees": assignees,
        })
else:
    token = os.getenv("JIRA_API_TOKEN")
    email = os.getenv("JIRA_EMAIL")
    base_url = os.getenv("JIRA_BASE_URL")
    for var, val in [("JIRA_API_TOKEN", token), ("JIRA_EMAIL", email), ("JIRA_BASE_URL", base_url)]:
        if not val:
            raise RuntimeError(f"{var} environment variable is not set")
    configs.append({"name": "default", "email": email, "token": token, "base_url": base_url})

app = FastAPI(title="Jira API Bridge")


@app.get("/", include_in_schema=False)
async def serve_ui():
    """Serve the single-page UI for tickets."""
    return FileResponse("static/index.html")


import base64

@app.get("/in-progress")
async def get_in_progress_issues():
    """
    Retrieve Jira issues with status 'In Progress' assigned to the configured users
    from each instance, returning a flat list of issue metadata.
    """
    headers = {"Content-Type": "application/json"}
    flattened = []
    async with httpx.AsyncClient() as client:
        for cfg in configs:
            autht=(cfg["email"], cfg["token"])
            auth=":".join(autht)
            auth64b = base64.b64encode(auth.encode())
            auth64 = auth64b.decode()

            # Build JQL without an explicit ORDER BY clause. We will sort the results
            # in Python after collecting issues from all instances so that the API
            # consumer still receives tickets ordered by their last update time.
            jql = 'status = "In Progress"'
            url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/myself"
            resp = await client.get(
                url,
                auth=autht,
                headers=headers,
            )

            # Output the Jira authentication status header for visibility.
            # The "X-Seraph-Loginreason" header is set by Jira and indicates why a
            # request may not have been authenticated (e.g. "OK" when auth
            # succeeds, "AUTHENTICATED_FAILED" when it doesn't).  Printing it
            # helps diagnose token / session problems without inspecting the
            # whole response in a debugger.
            print(f"[{cfg['name']}] X-Seraph-Loginreason: "
                  f"{resp.headers.get('X-Seraph-Loginreason')}")
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"{cfg['name']}: {resp.text}")
            data = resp.json()

            search_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search"
            resp = await client.get(
                search_url,
                auth=autht,
                headers=headers,
                params={"jql": jql},
            )

            print(search_url)

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"{cfg['name']}: {resp.text}")

            data = resp.json()
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                key = issue.get("key")
                project = fields.get("project", {}).get("key")
                assignee = fields.get("assignee")

                avatarUrl = None
                name = None
                if assignee:
                    name = assignee.get("displayName")
                    avatarUrls = assignee.get("avatarUrls")
                    if avatarUrls:
                        avatarUrl = avatarUrls.get("32x32")
                updated = fields.get("updated")
                # Jira's "duedate" field is an ISO-8601 date string (YYYY-MM-DD) or
                # None when no due date is set on the issue.
                due_date = fields.get("duedate")

                title = fields.get("summary")
                link = f"{cfg['base_url'].rstrip('/')}/browse/{key}"
                flattened.append({
                    "instance": cfg["name"],
                    "ticket": key,
                    "project": project,
                    "assignee": name, 
                    "avatarUrl": avatarUrl,
                    "updated": updated,
                    "dueDate": due_date,
                    "title": title,
                    "link": link,
                })
    # Sort the aggregated issues. Use the due date when present; fall back to the
    # last updated timestamp otherwise. Both Jira date strings (YYYY-MM-DD for
    # dueDate and the full ISO-8601 date-time for updated) compare correctly
    # lexicographically, so we can sort directly on the raw value.
    flattened.sort(key=lambda item: item.get("dueDate") or item.get("updated") or "")

    return flattened


# ---------------------------------------------------------------------------
# New route: /open-issues-by-due
# ---------------------------------------------------------------------------


@app.get("/open-issues-by-due")
async def get_open_issues_by_due_date():
    """Return issues that are *not* in a done / closed status.

    The query uses Jira's `statusCategory != Done` JQL filter which is more
    robust than explicitly excluding particular workflow statuses (e.g.
    "Done", "Closed", "Resolved") because it automatically captures every
    workflow status whose *category* is *Done* across all projects.  The
    returned list is aggregated across all configured Jira instances and
    sorted by the issue's due date in ascending order (soonest first).  Issues
    without a due date are placed at the end of the list.
    """

    headers = {"Content-Type": "application/json"}
    aggregated: list[dict] = []

    async with httpx.AsyncClient() as client:
        for cfg in configs:
            auth_tuple = (cfg["email"], cfg["token"])

            # JQL selects tickets that are not in a Done/Closed status category.
            # We deliberately omit an ORDER BY clause so that we can merge the
            # results from multiple instances first and then perform a single
            # global sort in Python.
            jql = "statusCategory != Done"

            search_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search"
            resp = await client.get(
                search_url,
                auth=auth_tuple,
                headers=headers,
                params={"jql": jql},
            )

            print(f"[{cfg['name']}] GET {search_url} → {resp.status_code}")

            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"{cfg['name']}: {resp.text}")

            data = resp.json()
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})

                key = issue.get("key")
                project = fields.get("project", {}).get("key")

                assignee = fields.get("assignee") or {}
                assignee_name = assignee.get("displayName")
                avatar_url = (assignee.get("avatarUrls") or {}).get("32x32")

                updated = fields.get("updated")
                due_date = fields.get("duedate")

                # Only keep issues that actually have a due date; skip others.
                if due_date:
                    aggregated.append({
                        "instance": cfg["name"],
                        "ticket": key,
                        "project": project,
                        "assignee": assignee_name,
                        "avatarUrl": avatar_url,
                        "updated": updated,
                        "dueDate": due_date,
                        "title": fields.get("summary"),
                        "link": f"{cfg['base_url'].rstrip('/')}/browse/{key}",
                    })

    # --------------------------------------------------
    # Sorting logic
    # --------------------------------------------------
    # We want *overdue* issues first, ordered by how overdue they are (oldest
    # due date first → most overdue).  After overdue items, show upcoming
    # issues ordered by soonest due date.  Items without a due date go last.

    from datetime import date

    today_str = date.today().isoformat()  # YYYY-MM-DD

    def sort_key(item: dict):
        due = item.get("dueDate")
        if due is None:
            # Bucket 2 → no due date (bottom)
            return (2, "9999-12-31")

        # Determine if the ticket is overdue relative to today (string compare
        # works because both are ISO dates).
        if due < today_str:
            # Bucket 0 → overdue (top), earlier due comes first
            return (0, due)
        # Bucket 1 → upcoming (middle), earlier due first
        return (1, due)

    aggregated.sort(key=sort_key)

    return aggregated
