from bbclient import BitbucketClient
import asyncio
import os

from rich import print
from rich.pretty import Pretty
import json
from typing import Dict, List, Any

workspace = "palliativa"
token = os.environ["BITBUCKET_API_TOKEN"]
repos = ["frontend", "backend"]
clients = [
    BitbucketClient(token=token, workspace=workspace, repo_slug=repo)
    for repo in repos
]

async def amain():

    # Build structured dashboard data: latest 10 pipelines per tag-category per repo
    categories = ["qa/v*", "staging/v*", "prod/v*"]
    dashboard: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for client in clients:
        repo_data: Dict[str, List[Dict[str, Any]]] = {}
        for pattern in categories:
            entries: List[Dict[str, Any]] = []
            async for pipeline in client.list_pipelines(
                target_selector_pattern=pattern,
                sort="-created_on",
                pagelen=10,
                max_items=10,
            ):
                ref = pipeline.get("target", {}).get("ref_name")
                completed = pipeline.get("completed_on")
                state = pipeline.get("state", {}) or {}
                result = None
                if isinstance(state, dict):
                    result = (
                        state.get("result", {}).get("name")
                        or state.get("name")
                        or state.get("type")
                    )
                # include commit details and a link to the commit in the UI
                commit = pipeline.get("target", {}).get("commit", {})
                commit_hash = commit.get("hash")
                commit_html = commit.get("links", {}).get("html", {}).get("href")
                entries.append({
                    "ref_name": ref,
                    "completed_on": completed,
                    "result": result,
                    "commit": commit_hash,
                    "commit_link": commit_html,
                })
            repo_data[pattern] = entries
        dashboard[client.repo_slug] = repo_data

    # Output JSON for dashboard consumption
    print(json.dumps(dashboard, indent=2))


if __name__ == "__main__":
    asyncio.run(amain())
