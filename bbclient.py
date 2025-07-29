import asyncio
import httpx
from typing import AsyncGenerator, Dict, List, Any, Optional, TypedDict
from urllib.parse import urlencode


class Environment(TypedDict):
    """Bitbucket environment object as returned by the API."""
    type: str
    uuid: str
    name: str

class SimpleType(TypedDict):
    """Generic object with only a 'type' field."""
    type: str

class PipelineConfigurationSource(TypedDict):
    source: str
    uri: str

class Pipeline(TypedDict):
    type: str
    uuid: str
    build_number: int
    creator: SimpleType
    repository: SimpleType
    target: SimpleType
    trigger: SimpleType
    state: SimpleType
    variables: List[SimpleType]
    created_on: str
    completed_on: str
    build_seconds_used: int
    configuration_sources: List[PipelineConfigurationSource]
    links: SimpleType

class BitbucketClient:
    BASE_URL = "https://api.bitbucket.org/2.0"

    def __init__(self, token: str, workspace: str, repo_slug: str):
        self.token = token
        self.workspace = workspace
        self.repo_slug = repo_slug
        self._client = httpx.AsyncClient(
                auth=("will@jjrsoftware.co.uk", self.token),
            base_url=self.BASE_URL,
            # headers={"Authorization": f"Bearer {self.token}"},
            timeout=20
        )

    async def close(self):
        await self._client.aclose()

    async def _get_paginated(
        self, path: str, max_items: Optional[int] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield items from all pages of a paginated Bitbucket endpoint, up to max_items if set."""
        url = path
        count = 0
        while url:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("values", []):
                if max_items is not None and count >= max_items:
                    return
                yield item
                count += 1
            url = data.get("next", None)

    async def list_environments(self) -> AsyncGenerator[Environment, None]:
        """Yield each environment in the repository."""
        path = f"/repositories/{self.workspace}/{self.repo_slug}/environments/"
        async for env in self._get_paginated(path):
            yield env  # type: ignore

    async def get_environment(self, environment_uuid: str) -> Environment:
        """Retrieve a single environment by UUID."""
        path = f"/repositories/{self.workspace}/{self.repo_slug}/environments/{environment_uuid}"
        resp = await self._client.get(path)
        resp.raise_for_status()
        return resp.json()

    async def list_deployments(self, limit: int = 10) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield deployments (newest first) in the repository."""
        path = f"/repositories/{self.workspace}/{self.repo_slug}/deployments/?sort=-created_on&pagelen={limit}"
        async for dep in self._get_paginated(path):
            yield dep


    async def get_deployment_statuses(
        self, deployment_uuid: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield all statuses for a given deployment UUID."""
        path = (
            f"/repositories/{self.workspace}/{self.repo_slug}"
            f"/deployments/{deployment_uuid}/statuses"
        )
        async for status in self._get_paginated(path):
            yield status

    async def list_pipelines(
        self,
        creator_uuid: Optional[str] = None,
        target_ref_type: Optional[str] = None,
        target_ref_name: Optional[str] = None,
        target_branch: Optional[str] = None,
        target_commit_hash: Optional[str] = None,
        target_selector_pattern: Optional[str] = None,
        target_selector_type: Optional[str] = None,
        created_on: Optional[str] = None,
        trigger_type: Optional[str] = None,
        status: Optional[str] = None,
        sort: Optional[str] = None,
        page: Optional[int] = None,
        pagelen: Optional[int] = None,
        max_items: Optional[int] = None,
    ) -> AsyncGenerator[Pipeline, None]:
        """
        Yield pipelines in the repository (one at a time), supporting filters, sorting, and max_items.

        Mirrors Bitbucket API query parameters: creator.uuid, target.ref_*,
        created_on, trigger_type, status, sort, page, pagelen.
        """
        params: Dict[str, Any] = {}
        if creator_uuid:
            params["creator.uuid"] = creator_uuid
        if target_ref_type:
            params["target.ref_type"] = target_ref_type
        if target_ref_name:
            params["target.ref_name"] = target_ref_name
        if target_branch:
            params["target.branch"] = target_branch
        if target_commit_hash:
            params["target.commit.hash"] = target_commit_hash
        if target_selector_pattern:
            params["target.selector.pattern"] = target_selector_pattern
        if target_selector_type:
            params["target.selector.type"] = target_selector_type
        if created_on:
            params["created_on"] = created_on
        if trigger_type:
            params["trigger_type"] = trigger_type
        if status:
            params["status"] = status
        if sort:
            params["sort"] = sort
        if page is not None:
            params["page"] = page
        if pagelen is not None:
            params["pagelen"] = pagelen

        path = f"/repositories/{self.workspace}/{self.repo_slug}/pipelines/"
        if params:
            path = f"{path}?{urlencode(params)}"
        async for pipeline in self._get_paginated(path, max_items=max_items):
            yield pipeline  # type: ignore



# --- Example usage ---
async def main():
    client = BitbucketClient(
        token="YOUR_BEARER_TOKEN",
        workspace="your-workspace",
        repo_slug="your-repo"
    )

    try:
        envs = await client.list_environments()
        print("Environments:")
        for e in envs:
            print(f"- {e['name']}")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

