from bbclient import BitbucketClient
import asyncio
import os

from rich import print
from rich.pretty import Pretty

bbc = BitbucketClient(token=os.environ["BITBUCKET_API_TOKEN"], workspace="palliativa",
                      repo_slug="frontend" )

async def amain():

    print( os.environ )

    print("Environments:")
    async for env in bbc.list_environments():
        print(Pretty(env))

    # List pipelines for this repo (newest first, top 5)
    print("\nPipelines (newest first, top 5):")
    async for pipeline in bbc.list_pipelines(sort="-created_on", pagelen=5, max_items=5):
        print(Pretty(pipeline))


if __name__ == "__main__":
    asyncio.run(amain())
