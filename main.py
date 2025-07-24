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
