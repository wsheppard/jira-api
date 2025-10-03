"""
Pipeline dashboard service: collects latest pipeline runs per tag-category across repos.
"""
from typing import Any, Dict, List

from bbclient import BitbucketClient, Pipeline


class PipelineDashboard:
    """Service to build structured dashboard data from Bitbucket pipelines."""

    def __init__(self, token: str, repo_full_names: List[str]) -> None:
        self.token = token
        self.repo_full_names = repo_full_names

    async def get_dashboard(
        self,
        categories: List[str],
        pagelen: int = 10,
        max_items: int = 10,
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """
        Return a nested dict mapping repo_slug -> tag-pattern -> list of pipelines.

        Each pipeline entry includes uuid, ref_name, completed_on, result, commit hash, commit link, and pipeline link.
        """
        dashboard: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for repo_full in self.repo_full_names:
            if "/" not in repo_full:
                continue
            workspace, repo_slug = repo_full.split("/", 1)
            client = BitbucketClient(token=self.token, workspace=workspace, repo_slug=repo_slug)
            repo_data: Dict[str, List[Dict[str, Any]]] = {}
            for pattern in categories:
                entries: List[Dict[str, Any]] = []
                async for pipeline in client.list_pipelines(
                    target_selector_pattern=pattern,
                    sort="-created_on",
                    pagelen=pagelen,
                    max_items=max_items,
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
                    commit = pipeline.get("target", {}).get("commit", {})
                    commit_hash = commit.get("hash")
                    commit_link = commit.get("links", {}).get("html", {}).get("href")
                    state_type = state.get("type")
                    state_name = state.get("name")
                    state_result_name = state.get("result", {}).get("name")

                    entries.append({
                        "uuid": pipeline.get("uuid"),
                        "ref_name": ref,
                        "completed_on": completed,
                        "result": result, # Keep existing for compatibility
                        "state_type": state_type,
                        "state_name": state_name,
                        "state_result_name": state_result_name,
                        "commit": commit_hash,
                        "commit_link": commit_link,
                        "pipeline_link": (
                            pipeline.get("links", {}).get("html", {}).get("href")
                            or pipeline.get("links", {}).get("self", {}).get("href")
                        ),
                    })
                repo_data[pattern] = entries
            await client.close()
            dashboard[repo_slug] = repo_data
        return dashboard
