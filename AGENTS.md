# AGENTS.md

This file contains instructions for the coding agent on how to work within this codebase.

## Coding Conventions

- Use `ruff` for linting and formatting.
- Line length: 120 characters.
- Target Python version: 3.11.
- Import sorting: Known first-party package is `backend`.

## Running the Application

- From the `backend/` directory run: `uvicorn main:app --reload` (API at http://localhost:8000)
- Frontend dev: `npm start` inside `frontend/` (CRA on http://localhost:3000)

## Runtime Boundary

- This is a read-only Palliativa delivery map.
- Jira, GitHub, and deployment data must come through `api-bridges`; this repository must not hold Jira, GitHub, DigitalOcean, or deployment credentials.
- Production release state comes from stable SemVer Git tags. Shared delivery state comes from `master` and exact Jira-key pull-request evidence.
- Feature-build provenance comes from Jira's `Feature builds` custom labels field and is related to deployments by exact image-tag equality.

## Deployment

- Use Docker Compose for deployment: `docker-compose up -d`
- Frontend is served by Caddy (see `Dockerfile.frontend` and `Caddyfile.frontend`); the FastAPI app no longer serves static files directly.
- Use the modern Docker Compose CLI with this repo's `compose.yml`.
- Default command: `docker compose up -d --build`

## Delivery Map Purpose

- Show the stable production release, current `master`, and current Jira tickets as one compact stack.
- Show exact pull-request, feature-build, and deployment evidence without exposing raw hashes, digests, or opaque IDs in the primary UI.
- Keep this site observational. Pull-request merge, release creation, builds, deployments, and Jira mutation belong to their established owning workflows.

## Semantic Index (feature → code map)

- Backend API: `backend/main.py` – FastAPI aggregation of typed `api-bridges` read contracts at `/delivery-stack`.
- Frontend entry: `frontend/src/index.js` – mounts `<App />`, loads Bootstrap CSS/JS.
- Frontend shell: `frontend/src/App.js` – compact read-only production → master → ticket delivery stack.
- Frontend styles: `frontend/src/App.css` – wide reusable ticket rows and responsive release/deployment summary components.

## Gemini Notes (Jira API)

- Jira Cloud REST API v2 is deprecated; always use v3 endpoints.
- JQL search should hit `/rest/api/3/search/jql` per Atlassian changelog (https://developer.atlassian.com/changelog/#CHANGE-2046).
