import asyncio
import httpx
from typing import Dict, List, Any, Optional


class BitbucketClient:
    BASE_URL = "https://api.bitbucket.org/2.0"

    def __init__(self, token: str, workspace: str, repo_slug: str):
        self.token = token
        self.workspace = workspace
        self.repo_slug = repo_slug
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=20
        )

    async def close(self):
        await self._client.aclose()

    async def _get_paginated(self, path: str) -> List[Dict[str, Any]]:
        """Fetch all pages of a paginated Bitbucket endpoint."""
        url = path
        results = []
        while url:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("values", []))
            url = data.get("next", None)
        return results

    async def list_environments(self) -> List[Dict[str, Any]]:
        path = f"/repositories/{self.workspace}/{self.repo_slug}/environments/"
        return await self._get_paginated(path)

    async def list_deployments(self, limit: int = 10) -> List[Dict[str, Any]]:
        """List deployments sorted by newest first."""
        path = f"/repositories/{self.workspace}/{self.repo_slug}/deployments/?sort=-created_on&pagelen={limit}"
        return await self._get_paginated(path)

    async def get_deployment_statuses(self, deployment_uuid: str) -> List[Dict[str, Any]]:
        path = f"/repositories/{self.workspace}/{self.repo_slug}/deployments/{deployment_uuid}/statuses"
        return await self._get_paginated(path)

    async def get_latest_successful_per_env(self) -> Dict[str, Dict[str, Any]]:
        deployments = await self.list_deployments(limit=20)
        env_latest: Dict[str, Dict[str, Any]] = {}

        for dep in deployments:
            env = dep.get("environment", {}).get("name")
            if not env or env in env_latest:
                continue

            statuses = await self.get_deployment_statuses(dep["uuid"])
            if any(s["state"] == "SUCCESSFUL" for s in statuses):
                env_latest[env] = {
                    "uuid": dep["uuid"],
                    "commit": dep.get("commit", {}).get("hash"),
                    "created_on": dep["created_on"]
                }

        return env_latest


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

        latest = await client.get_latest_successful_per_env()
        print("\nLatest deployments per environment:")
        for env, info in latest.items():
            print(f"{env}: {info}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

