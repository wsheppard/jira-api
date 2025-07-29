from bbclient import BitbucketClient
import asyncio
import os

from rich import print
from rich.pretty import Pretty

bbc = BitbucketClient(token=os.environ["BITBUCKET_API_TOKEN"], workspace="palliativa",
                      repo_slug="frontend" )

async def amain():

    print( os.environ )

    print("Environments and associated pipeline (per lock):")
    async for env in bbc.list_environments():
        print(Pretty(env))
        pipeline_uuid = (
            env.get("lock", {})
               .get("lock_opener", {})
               .get("pipeline_uuid")
        )
        if pipeline_uuid:
            print(f"  Pipeline for pipeline_uuid={pipeline_uuid}:")
            pipeline = await bbc.get_pipeline(pipeline_uuid)
            print(Pretty(pipeline))


if __name__ == "__main__":
    asyncio.run(amain())
