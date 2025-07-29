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

    # Demonstrate the latest pipelines for each tag category (qa, staging, prod)
    categories = ["qa/v*", "staging/v*", "prod/v*"]
    print("\nLatest pipeline by tag pattern:")
    for pattern in categories:
        pipeline = await bbc.get_latest_pipeline_for_pattern(pattern, pagelen=5)
        print(f"{pattern}: {Pretty(pipeline) if pipeline else 'No pipeline found'}")

    # Discover which environment is locked to each of those latest pipelines
    print("\nEnvironment lock state for latest pipelines:")
    for pattern in categories:
        pipeline = await bbc.get_latest_pipeline_for_pattern(pattern, pagelen=5)
        if not pipeline:
            print(f"{pattern}: no pipeline found → env N/A")
            continue
        locked_env = None
        async for env in bbc.list_environments():
            if env.get("lock", {}).get("lock_opener", {}).get("pipeline_uuid") == pipeline["uuid"]:
                locked_env = env
                break
        if locked_env:
            print(f"{pattern}: deployed to environment {Pretty(locked_env)}")
        else:
            print(f"{pattern}: pipeline {pipeline['uuid']} not locked in any environment")


if __name__ == "__main__":
    asyncio.run(amain())
