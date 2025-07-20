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

            print("The Jira Token: {}".format(cfg["token"]))

            auth=cfg["email"] + ":" + cfg["token"]

            print(f"AUTH WITH EXTENTS: [{auth}]")

            auth64b = base64.b64encode(auth.encode())
            auth64 = auth64b.decode()

            print(auth64)

            headers["Authorization"] = f"Basic {auth64}"

            print(headers)

            if len(cfg["assignees"]) == 1:
                assignee_clause = f'assignee = "{cfg["assignees"][0]}"'
            else:
                users = ", ".join(f'"{u}"' for u in cfg["assignees"])
                assignee_clause = f'assignee in ({users})'
            jql = f'status = "In Progress" AND {assignee_clause}'
            url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/myself"
            resp = await client.get(
                url,
                headers=headers,
            )

            print(url)

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

            print(data)
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                key = issue.get("key")
                project = fields.get("project", {}).get("key")
                assignee = fields.get("assignee", {}).get("emailAddress")
                title = fields.get("summary")
                link = f"{cfg['base_url'].rstrip('/')}/browse/{key}"
                flattened.append({
                    "instance": cfg["name"],
                    "ticket": key,
                    "project": project,
                    "assignee": assignee,
                    "title": title,
                    "link": link,
                })
    return flattened
