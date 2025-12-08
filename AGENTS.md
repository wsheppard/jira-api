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

## Environment Setup

- Copy `.env.example` to `.env` and fill in API tokens for Jira and Bitbucket.
- Install dependencies with `pip install -r requirements.txt`

## Deployment

- Use Docker Compose for deployment: `docker-compose up -d`
- Ensure a Docker network named `docker_static` exists.
- Frontend is served by Caddy (see `Dockerfile.frontend` and `Caddyfile.frontend`); the FastAPI app no longer serves static files directly.

## Semantic Index (feature → code map)

- Backend API: `backend/main.py` – FastAPI app exposing Jira views (`/open-issues-by-due`, `/in-progress`, `/backlog`, `/manager-meeting`, `/recently-updated`) plus Bitbucket utilities (`/bitbucket-test`, `/bitbucket-commits`, `/bitbucket-repos`) and pipeline/deployment data (`/pipeline-dashboard`, `/deployments`).
- Bitbucket pipeline aggregator: `backend/pipeline_dashboard.py` – wraps `BitbucketClient` to fetch pipelines per repo/tag pattern and shape data for the pipeline dashboard.
- Jira/Bitbucket client: `backend/bbclient.py` – Bitbucket REST helper and pipeline iterator used by the pipeline dashboard.
- Frontend entry: `frontend/src/index.js` – mounts `<App />`, loads Bootstrap CSS/JS.
- Frontend shell: `frontend/src/App.js` – SPA controller with view selector, polling (30s), URL syncing (`/` or `/view/{id}`), error/loading state, and routing between tickets and pipeline data.
- Tickets UI: `frontend/src/TicketsList.js` – card grid with overdue wiggle animation, priority borders, labels, assignee avatar, and time-ago metadata.
- Pipeline UI: `frontend/src/PipelineDashboard.js` – per-environment summary of latest successful frontend/backend runs plus detailed tables with result badges, links, and manual-step callouts.
- Frontend styles: `frontend/src/App.css` – grid layout, wiggle animation for stale tickets, priority color stripes, and pipeline ref badge styling.
