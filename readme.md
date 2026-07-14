# Palliativa Delivery Map

Read-only production, `master`, ticket, feature-build, pull-request, and deployment visibility for Palliativa.

The FastAPI backend reads typed Jira, GitHub, and infra-control data through `api-bridges`. This repository does not hold service credentials and does not provide merge, build, deployment, release, or Jira mutation controls.

## Run

Use the repository's Compose stack:

```bash
docker compose up -d --build
```

The frontend is served at `https://jira.dev.jjrsoftware.co.uk` and reads the delivery API at `https://jira.api.jjrsoftware.co.uk/delivery-stack`.
