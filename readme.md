# Jira API FastAPI Bridge

Super simple FastAPI bridge for various Jira things.

## Setup

Create a `.env` file in the project root with your Jira Cloud credentials. For a single instance:

```ini
JIRA_API_TOKEN="your_api_token"
JIRA_EMAIL="your_jira_account_email"
JIRA_BASE_URL="https://your-domain.atlassian.net"
```

If you have multiple Jira instances, prefix each set of creds and list their names in `JIRA_INSTANCES`:

```ini
JIRA_INSTANCES="FIRST,SECOND"

FIRST_JIRA_API_TOKEN="token_for_first"
FIRST_JIRA_EMAIL="your_email@first.example"
FIRST_JIRA_BASE_URL="https://first-domain.atlassian.net"
# optionally include issues assigned to steph as well as will:
FIRST_JIRA_ASSIGNEES="your_email@first.example,steph@jjrsoftware.co.uk"

SECOND_JIRA_API_TOKEN="token_for_second"
SECOND_JIRA_EMAIL="your_email@second.example"
SECOND_JIRA_BASE_URL="https://second-domain.atlassian.net"
```

Install the required dependencies:

```bash
pip install fastapi uvicorn python-dotenv httpx
```

## Usage


### Run the server

Start the FastAPI server (or in Docker Compose) and then open your browser at http://localhost:8000:

```bash
uvicorn main:app --reload
```

Open the browser to view the single-page UI using Bootstrap cards:

```bash
open http://localhost:8000
```

## Docker Compose Deployment

Ensure you have an external Docker network named `docker_static`:

```bash
docker network create docker_static
```

Start the service via Docker Compose (it will build the image, attach it to `docker_static`, and expose port 8000):

```bash
docker-compose up -d
```
