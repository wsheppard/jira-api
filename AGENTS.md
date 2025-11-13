# AGENTS.md

This file contains instructions for the coding agent on how to work within this codebase.

## Coding Conventions

- Use `ruff` for linting and formatting.
- Line length: 120 characters.
- Target Python version: 3.11.
- Import sorting: Known first-party modules include `bbclient`, `pipeline_dashboard`, and `main`.

## Running the Application

- Start the server with: `uvicorn main:app --reload`
- Open the browser at http://localhost:8000 to view the UI.

## Environment Setup

- Copy `.env.example` to `.env` and fill in API tokens for Jira and Bitbucket.
- Install dependencies with `pip install -r requirements.txt`

## Deployment

- Use Docker Compose for deployment: `docker-compose up -d`
- Ensure a Docker network named `docker_static` exists.
