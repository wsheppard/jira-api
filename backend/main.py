"""FastAPI bridge for Jira and GitHub workflows."""

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import date
from typing import Any, Dict, List, Literal, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

JIRA_KEY_REGEX = re.compile(r"\b((?:AP|PD)-\d+)\b")
GENERIC_JIRA_KEY_REGEX = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
QUESTION_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "can",
    "could",
    "do",
    "does",
    "for",
    "from",
    "have",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "me",
    "of",
    "on",
    "or",
    "please",
    "show",
    "that",
    "the",
    "there",
    "ticket",
    "tickets",
    "to",
    "we",
    "what",
    "where",
    "which",
    "who",
    "with",
    "would",
}

# reduce noisy dependency logging; disable httpx chatter entirely
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("httpcore").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# module logger
logger = logging.getLogger(__name__)

print("--- JIRA-API: Application starting up ---")

# ---------------------------------------------------------------------------
# Jira configuration
# ---------------------------------------------------------------------------


# Load .env and require a single Atlassian API token for all requests
load_dotenv()
# Hard-code Atlassian email and require separate API tokens for Jira and GitHub
email = "will@jjrsoftware.co.uk"
jira_token = os.getenv("JIRA_API_TOKEN")
if not jira_token:
    raise RuntimeError("JIRA_API_TOKEN environment variable must be set")
github_token = os.getenv("GITHUB_TOKEN")
if not github_token:
    raise RuntimeError("GITHUB_TOKEN environment variable must be set")
digitalocean_token = os.getenv("DIGITALOCEAN_API_TOKEN", "").strip()
digitalocean_registry = os.getenv("DIGITALOCEAN_CONTAINER_REGISTRY", "jjr-repo-1").strip()
openai_api_key = os.getenv("OPENAI_API_KEY")
openai_jql_model = os.getenv("OPENAI_JQL_MODEL", "gpt-4.1-mini")
xai_api_key = os.getenv("XAI_API_KEY")
xai_jql_model = os.getenv("XAI_JQL_MODEL", "grok-3-mini")
if not openai_api_key and not xai_api_key:
    raise RuntimeError("One of OPENAI_API_KEY or XAI_API_KEY environment variables must be set")

# Hard-code Jira instances
configs: List[Dict[str, Any]] = [
    {"name": "palliativa", "email": email, "token": jira_token, "base_url": "https://palliativa.atlassian.net"},
    {"name": "jjrsoftware", "email": email, "token": jira_token, "base_url": "https://jjrsoftware.atlassian.net"},
]

# Cache Jira results to avoid thrashing the API; configure TTL via env var.
JIRA_CACHE_TTL_SECONDS = int(os.getenv("JIRA_CACHE_TTL_SECONDS", "20"))
_jira_cache: Dict[Tuple[str, Tuple[str, ...]], Tuple[float, List[Dict[str, Any]]]] = {}
_jira_cache_lock = asyncio.Lock()
GITHUB_COMPARE_CACHE_TTL_SECONDS = int(os.getenv("GITHUB_COMPARE_CACHE_TTL_SECONDS", "120"))
_github_compare_cache: Dict[Tuple[str, str, str, str], Tuple[float, Dict[str, Any]]] = {}
_github_compare_cache_lock = asyncio.Lock()
GITHUB_TAGS_CACHE_TTL_SECONDS = int(os.getenv("GITHUB_TAGS_CACHE_TTL_SECONDS", "900"))
_github_tags_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, List[str]]]] = {}
_github_tags_cache_lock = asyncio.Lock()
GITHUB_PR_CACHE_TTL_SECONDS = int(os.getenv("GITHUB_PR_CACHE_TTL_SECONDS", "900"))
_github_pr_detail_cache: Dict[Tuple[str, str, int], Tuple[float, Dict[str, str] | None]] = {}
_github_pr_detail_cache_lock = asyncio.Lock()
_github_pr_commits_cache: Dict[Tuple[str, str, int], Tuple[float, List[Dict[str, str]]]] = {}
_github_pr_commits_cache_lock = asyncio.Lock()
STAGING_TICKETS_CACHE_TTL_SECONDS = int(os.getenv("STAGING_TICKETS_CACHE_TTL_SECONDS", "120"))
_staging_tickets_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_staging_tickets_cache_lock = asyncio.Lock()
TICKET_SUMMARY_CACHE_TTL_SECONDS = int(os.getenv("TICKET_SUMMARY_CACHE_TTL_SECONDS", "21600"))
_ticket_summary_cache: Dict[Tuple[str, str, str, str], Tuple[float, str]] = {}
_ticket_summary_cache_lock = asyncio.Lock()
TICKET_ASSISTANT_APPROVAL_TTL_SECONDS = int(os.getenv("TICKET_ASSISTANT_APPROVAL_TTL_SECONDS", "1800"))
_ticket_assistant_approvals: Dict[str, Tuple[float, str, str]] = {}
_ticket_assistant_approvals_lock = asyncio.Lock()


class TicketQuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    limit: int = Field(default=20, ge=1, le=100)


class TicketQuestionResponse(BaseModel):
    question: str
    interpretation: str
    jql: str
    attempted_jql: List[str]
    successful_jql: List[str]
    total_matches: int
    limited_to: int
    tickets: List[Dict[str, Any]]


class ImplementationTicketDraft(BaseModel):
    summary: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=20000)
    issue_type: str = Field(default="Task", min_length=1, max_length=100)
    labels: List[str] = Field(default_factory=list)


class CreateLinkedSoftwareTicketsRequest(BaseModel):
    design_ticket_key: str = Field(min_length=3, max_length=40)
    target_project: str = Field(default="AP", min_length=2, max_length=20)
    implementations: List[ImplementationTicketDraft] = Field(min_length=1, max_length=20)
    link_type: str = Field(default="Relates", min_length=1, max_length=100)
    dry_run: bool = True


class CreateLinkedSoftwareTicketsResponse(BaseModel):
    design_ticket_key: str
    design_ticket_link: str
    target_project: str
    dry_run: bool
    created_count: int
    tickets: List[Dict[str, Any]]


class TicketAssistantRequest(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    limit: int = Field(default=20, ge=1, le=100)
    dry_run: bool = True
    approval_token: str = Field(default="", max_length=100)


class TicketAssistantResponse(BaseModel):
    intent: str
    interpretation: str
    message: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)


def _assistant_text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


async def _mint_ticket_assistant_approval_token(intent: str, text: str) -> tuple[str, int]:
    token = uuid.uuid4().hex
    expires_at = time.monotonic() + TICKET_ASSISTANT_APPROVAL_TTL_SECONDS
    fingerprint = _assistant_text_fingerprint(text)
    async with _ticket_assistant_approvals_lock:
        _ticket_assistant_approvals[token] = (expires_at, intent, fingerprint)
    return token, TICKET_ASSISTANT_APPROVAL_TTL_SECONDS


async def _consume_ticket_assistant_approval_token(token: str, intent: str, text: str) -> bool:
    normalized = token.strip()
    if not normalized:
        return False
    now = time.monotonic()
    expected_fingerprint = _assistant_text_fingerprint(text)
    async with _ticket_assistant_approvals_lock:
        stale_tokens = [key for key, (expires_at, _, _) in _ticket_assistant_approvals.items() if expires_at <= now]
        for stale in stale_tokens:
            _ticket_assistant_approvals.pop(stale, None)
        payload = _ticket_assistant_approvals.get(normalized)
        if not payload:
            return False
        expires_at, stored_intent, stored_fingerprint = payload
        if expires_at <= now or stored_intent != intent or stored_fingerprint != expected_fingerprint:
            return False
        _ticket_assistant_approvals.pop(normalized, None)
        return True


async def _merge_github_pr(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    owner: str,
    repo: str,
    pr_number: int,
    method: Literal["merge", "squash", "rebase"],
) -> Dict[str, Any]:
    detail_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    detail_resp = await client.get(detail_url, headers=headers)
    if detail_resp.status_code != 200:
        raise HTTPException(status_code=detail_resp.status_code, detail=detail_resp.text)
    pr = detail_resp.json() or {}

    merge_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/merge"
    merge_resp = await client.put(merge_url, headers=headers, json={"merge_method": method})
    if merge_resp.status_code not in (200, 201):
        raise HTTPException(status_code=merge_resp.status_code, detail=merge_resp.text)
    merge_payload = merge_resp.json() or {}

    return {
        "number": pr_number,
        "title": pr.get("title") or "",
        "url": pr.get("html_url") or "",
        "head": ((pr.get("head") or {}).get("ref") or ""),
        "merged": bool(merge_payload.get("merged")),
        "message": merge_payload.get("message") or "",
        "sha": merge_payload.get("sha") or "",
    }


def _adf_to_text(node: Any) -> str:
    """Best-effort conversion of Atlassian Document Format to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(child) for child in node)
    if isinstance(node, dict):
        node_type = node.get("type")
        # Text node
        if node_type == "text":
            return node.get("text", "")
        # Hard/soft breaks
        if node_type in {"hardBreak", "paragraph", "heading", "bulletList", "orderedList", "listItem"}:
            content = node.get("content") or []
            separator = "\n" if node_type in {"paragraph", "heading", "listItem"} else ""
            return separator.join(filter(None, (_adf_to_text(c) for c in content)))
        # If it's a container with content
        content = node.get("content")
        if content:
            return "".join(_adf_to_text(c) for c in content)
    return ""


def _latest_comment(issue_fields: Dict[str, Any]) -> Dict[str, Any] | None:
    comments_block = issue_fields.get("comment") or {}
    comments = comments_block.get("comments") or []
    if not comments:
        return None
    latest = max(comments, key=lambda c: c.get("updated") or c.get("created") or "")
    body_raw = latest.get("body")
    body_text = _adf_to_text(body_raw).strip()
    if len(body_text) > 300:
        body_text = body_text[:297] + "..."
    return {
        "author": (latest.get("author") or {}).get("displayName"),
        "created": latest.get("created") or latest.get("updated"),
        "body": body_text,
    }


async def _generate_ticket_audience_summary(
    ticket_key: str,
    updated: str,
    summary: str,
    status: str,
    description: str,
    latest_comment: str,
) -> str:
    provider, _, model, _ = _provider_config()
    cache_key = (ticket_key.upper(), updated or "", provider, model)
    if TICKET_SUMMARY_CACHE_TTL_SECONDS > 0:
        now = time.monotonic()
        async with _ticket_summary_cache_lock:
            cached = _ticket_summary_cache.get(cache_key)
            if cached:
                cached_at, cached_summary = cached
                if now - cached_at < TICKET_SUMMARY_CACHE_TTL_SECONDS:
                    return cached_summary
                _ticket_summary_cache.pop(cache_key, None)

    system_prompt = (
        "You summarize Jira tickets for a non-technical audience. "
        "Return strict JSON with one key: summary. "
        "The summary must be plain text, one paragraph, no markdown. "
        "Do not add ellipses unless they appear in source text. "
        "Include current status and the latest meaningful progress/risk from the latest comment when present. "
        "Do not invent facts."
    )
    user_prompt = (
        f"Ticket: {ticket_key}\n"
        f"Status: {status or 'Unknown'}\n"
        f"Title: {summary or ''}\n"
        f"Description: {description or ''}\n"
        f"Latest comment: {latest_comment or ''}\n"
    )
    parsed = await _call_llm_json(system_prompt, user_prompt)
    candidate = str(parsed.get("summary") or "").strip()
    if not candidate:
        raise HTTPException(status_code=502, detail=f"LLM summary missing for ticket {ticket_key}")
    compact = " ".join(candidate.split())

    if TICKET_SUMMARY_CACHE_TTL_SECONDS > 0:
        async with _ticket_summary_cache_lock:
            _ticket_summary_cache[cache_key] = (time.monotonic(), compact)
    return compact

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


app = FastAPI(title="Jira / GitHub Bridge")


# Add CORS middleware to allow requests from the frontend service
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://jira.dev.jjrsoftware.co.uk",
        "https://jira.api.jjrsoftware.co.uk",
        "http://localhost:3000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Jira endpoints
# ---------------------------------------------------------------------------


async def _search_jira(jql: str, fields: List[str]) -> List[Dict[str, Any]]:
    """Run a JQL search across all configured Jira instances and return a
    flattened list of issues.
    """
    cache_key = (jql, tuple(fields))
    if JIRA_CACHE_TTL_SECONDS > 0:
        now = time.monotonic()
        async with _jira_cache_lock:
            cached = _jira_cache.get(cache_key)
            if cached:
                cached_at, cached_payload = cached
                if now - cached_at < JIRA_CACHE_TTL_SECONDS:
                    logger.info("Serving cached Jira response for JQL: %s", jql)
                    return copy.deepcopy(cached_payload)
                _jira_cache.pop(cache_key, None)

    flattened: List[Dict[str, Any]] = []
    headers = {"Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:
        for cfg in configs:
            json_data = {"jql": jql, "fields": fields}
            search_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search/jql"
            logger.info("Querying Jira [%s]: %s", cfg["name"], jql)
            resp = await client.post(search_url, auth=(cfg["email"], cfg["token"]),
                                     headers=headers, json=json_data)

            if resp.status_code != 200:
                print(f"--- JIRA ERROR ---")
                print(f"Response body:\n{resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

            for issue in resp.json().get("issues", []):
                issue_fields = issue.get("fields", {})
                assignee = issue_fields.get("assignee") or {}
                avatar = (assignee.get("avatarUrls") or {}).get("32x32")
                priority = (issue_fields.get("priority") or {}).get("name")
                issuetype = (issue_fields.get("issuetype") or {}).get("name")

                latest_comment = _latest_comment(issue_fields)
                description_text = _adf_to_text(issue_fields.get("description")).strip()
                if len(description_text) > 600:
                    description_text = description_text[:597] + "..."
                status = issue_fields.get("status") or {}
                status_category = (status.get("statusCategory") or {}).get("name")

                flattened.append(
                    {
                        "instance": cfg["name"],
                        "ticket": issue.get("key"),
                        "project": issue_fields.get("project", {}).get("key"),
                        "assignee": assignee.get("displayName"),
                        "avatarUrl": avatar,
                        "updated": issue_fields.get("updated"),
                        "dueDate": issue_fields.get("duedate"),
                        "title": issue_fields.get("summary"),
                        "link": f"{cfg['base_url'].rstrip('/')}/browse/{issue.get('key')}",
                        "priority": priority,
                        "labels": issue_fields.get("labels", []),
                        "issuetype": issuetype,
                        "statusName": status.get("name"),
                        "statusCategory": status_category,
                        "latestComment": latest_comment,
                        "descriptionText": description_text,
                    }
                )
    if JIRA_CACHE_TTL_SECONDS > 0:
        async with _jira_cache_lock:
            _jira_cache[cache_key] = (time.monotonic(), copy.deepcopy(flattened))
    return flattened


def _extract_question_terms(question: str) -> List[str]:
    raw_terms = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9._/-]*", question.lower())
    terms: List[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        if len(term) < 3 or term in QUESTION_STOP_WORDS:
            continue
        if term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


def _escape_jql_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _fallback_jql_candidates(question: str) -> List[str]:
    normalized = " ".join(question.strip().split())
    if not normalized:
        return []

    candidates: List[str] = []
    keys = sorted(set(GENERIC_JIRA_KEY_REGEX.findall(normalized.upper())))
    if keys:
        key_list = ", ".join(keys)
        candidates.append(f"key in ({key_list}) ORDER BY updated DESC")

    terms = _extract_question_terms(normalized)
    if terms:
        top_terms = terms[:4]
        clauses = [f'text ~ "{_escape_jql_value(term)}"' for term in top_terms]
        candidates.append(f"({ ' OR '.join(clauses) }) ORDER BY updated DESC")

    candidates.append(f'text ~ "{_escape_jql_value(normalized[:120])}" ORDER BY updated DESC')
    return candidates


def _provider_config() -> Tuple[str, str, str, str]:
    provider = "openai" if openai_api_key else "xai"
    provider_api_key = openai_api_key if openai_api_key else xai_api_key
    model = openai_jql_model if provider == "openai" else xai_jql_model
    api_base_url = "https://api.openai.com/v1" if provider == "openai" else "https://api.x.ai/v1"
    return provider, provider_api_key or "", model, api_base_url


async def _call_llm_json(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    _, provider_api_key, model, api_base_url = _provider_config()
    system_prompt = (
        system_prompt.strip()
    )

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {provider_api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{api_base_url}/chat/completions", headers=headers, json=payload)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {resp.text}")

    content = (((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=502, detail="LLM returned empty content")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"LLM returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="LLM JSON response must be an object")
    return parsed


async def _question_to_jql_candidates(question: str) -> Dict[str, Any]:
    system_prompt = (
        "You are a Jira query planner. Convert a user ticket question into JQL candidates. "
        "Return strict JSON with keys: interpretation, jql_candidates. "
        "jql_candidates must be a JSON array of 1 to 3 Jira Cloud JQL strings. "
        "No markdown, no prose outside JSON. "
        "Prefer recall over precision if the user asks whether a ticket exists."
    )
    user_prompt = (
        "User question:\n"
        f"{question.strip()}\n\n"
        "Use ORDER BY updated DESC unless a different order is explicitly requested."
    )
    parsed = await _call_llm_json(system_prompt, user_prompt)

    interpretation = str(parsed.get("interpretation") or "").strip()
    raw_candidates = parsed.get("jql_candidates")
    if isinstance(raw_candidates, str):
        candidates = [raw_candidates]
    elif isinstance(raw_candidates, list):
        candidates = [str(item) for item in raw_candidates if str(item).strip()]
    else:
        candidates = []

    normalized_candidates: List[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip().rstrip(";")
        if not normalized or normalized in seen:
            continue
        normalized_candidates.append(normalized)
        seen.add(normalized)

    return {"interpretation": interpretation, "jql_candidates": normalized_candidates[:3]}


async def _infer_assistant_action(text: str) -> Dict[str, Any]:
    system_prompt = (
        "You are a Jira assistant router. Infer user intent from free text. "
        "Return strict JSON with keys: intent, interpretation, search, create. "
        "intent must be one of: search_tickets, create_linked_software_tickets. "
        "search must include question. "
        "create must include design_ticket_key, target_project, link_type, implementations. "
        "implementations is an array of objects with: summary, description, issue_type, labels. "
        "No markdown and no extra keys."
    )
    user_prompt = f"User input:\n{text.strip()}"
    parsed = await _call_llm_json(system_prompt, user_prompt)
    intent = str(parsed.get("intent") or "").strip()
    interpretation = str(parsed.get("interpretation") or "").strip()
    search = parsed.get("search") if isinstance(parsed.get("search"), dict) else {}
    create = parsed.get("create") if isinstance(parsed.get("create"), dict) else {}
    if intent not in {"search_tickets", "create_linked_software_tickets"}:
        intent = "search_tickets"
    return {"intent": intent, "interpretation": interpretation, "search": search, "create": create}


def _ticket_relevance_score(ticket: Dict[str, Any], question: str, terms: List[str], mentioned_keys: set[str]) -> int:
    score = 0
    ticket_key = str(ticket.get("ticket") or "").upper()
    title = str(ticket.get("title") or "").lower()
    status = str(ticket.get("statusName") or "").lower()
    labels = [str(label).lower() for label in (ticket.get("labels") or [])]
    question_lower = question.lower()

    if ticket_key and ticket_key in mentioned_keys:
        score += 100
    if ticket_key and ticket_key.lower() in question_lower:
        score += 60
    for term in terms:
        if term in title:
            score += 12
        if term in status:
            score += 6
        if any(term in label for label in labels):
            score += 4
    if ticket.get("statusCategory") == "In Progress":
        score += 2
    return score


def _ticket_term_match_count(ticket: Dict[str, Any], terms: List[str]) -> int:
    if not terms:
        return 0
    title = str(ticket.get("title") or "").lower()
    status = str(ticket.get("statusName") or "").lower()
    labels = [str(label).lower() for label in (ticket.get("labels") or [])]
    latest_comment = str((ticket.get("latestComment") or {}).get("body") or "").lower()
    matched: set[str] = set()
    for term in terms:
        if term in title or term in status or term in latest_comment or any(term in label for label in labels):
            matched.add(term)
    return len(matched)


async def _find_issue_and_config(
    key: str, fields: List[str]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    issue_key = key.strip().upper()
    async with httpx.AsyncClient() as client:
        for cfg in configs:
            issue_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/issue/{issue_key}"
            resp = await client.get(
                issue_url,
                auth=(cfg["email"], cfg["token"]),
                headers=headers,
                params={"fields": ",".join(fields)},
            )
            if resp.status_code == 200:
                return cfg, resp.json()
            if resp.status_code == 404:
                continue
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
    raise HTTPException(status_code=404, detail=f"Ticket '{issue_key}' not found in configured Jira instances")


def _dedupe_labels(labels: List[str]) -> List[str]:
    output: List[str] = []
    seen: set[str] = set()
    for label in labels:
        value = (label or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        output.append(value)
    return output


def _model_to_dict(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[attr-defined]
    if hasattr(model, "dict"):
        return model.dict()  # type: ignore[attr-defined]
    raise RuntimeError("Unsupported model type for serialization")


async def _llm_filter_and_rank_ticket_shortlist(
    question: str, shortlist: List[Dict[str, Any]], limit: int
) -> List[str]:
    compact_tickets = []
    for ticket in shortlist:
        ticket_id = f"{ticket.get('instance')}::{ticket.get('ticket')}"
        compact_tickets.append(
            {
                "id": ticket_id,
                "ticket": ticket.get("ticket"),
                "instance": ticket.get("instance"),
                "title": ticket.get("title"),
                "status": ticket.get("statusName"),
                "labels": ticket.get("labels") or [],
                "description": ticket.get("descriptionText") or "",
                "latest_comment": (ticket.get("latestComment") or {}).get("body") or "",
                "updated": ticket.get("updated"),
            }
        )

    system_prompt = (
        "You are ranking Jira tickets by relevance to a user question. "
        "Return strict JSON with key ranked_ids (array of relevant ticket IDs in best-first order). "
        "Include only genuinely relevant tickets. "
        f"Return at most {limit} IDs. "
        "Use only provided ticket data. No markdown. No extra keys."
    )
    user_prompt = (
        "Question:\n"
        f"{question}\n\n"
        "Ticket shortlist JSON:\n"
        f"{json.dumps(compact_tickets)}"
    )
    parsed = await _call_llm_json(system_prompt, user_prompt)
    ranked_ids_raw = parsed.get("ranked_ids")
    if not isinstance(ranked_ids_raw, list):
        raise HTTPException(status_code=502, detail="LLM reranker did not return ranked_ids array")

    seen: set[str] = set()
    ranked_ids: List[str] = []
    for item in ranked_ids_raw:
        value = str(item).strip()
        if not value or value in seen:
            continue
        ranked_ids.append(value)
        seen.add(value)
    return ranked_ids


@app.get("/llm-status")
async def llm_status() -> Dict[str, str]:
    provider = "openai" if openai_api_key else "xai"
    model = openai_jql_model if provider == "openai" else xai_jql_model
    return {"provider": provider, "model": model}


@app.post("/ticket-question", response_model=TicketQuestionResponse)
async def ticket_question(payload: TicketQuestionRequest) -> TicketQuestionResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    resolved = await _question_to_jql_candidates(question)
    llm_candidates = list(resolved.get("jql_candidates") or [])
    fallback_candidates = _fallback_jql_candidates(question)

    attempted_jql: List[str] = []
    seen_jql: set[str] = set()
    for candidate in [*llm_candidates, *fallback_candidates]:
        normalized = candidate.strip().rstrip(";")
        if not normalized or normalized in seen_jql:
            continue
        attempted_jql.append(normalized)
        seen_jql.add(normalized)
    attempted_jql = attempted_jql[:6]

    fields = [
        "summary",
        "description",
        "comment",
        "project",
        "assignee",
        "updated",
        "duedate",
        "key",
        "status",
        "priority",
        "labels",
        "issuetype",
    ]
    gathered: Dict[Tuple[str, str], Dict[str, Any]] = {}
    successful_jql: List[str] = []
    for candidate in attempted_jql:
        try:
            matches = await _search_jira(candidate, fields)
        except HTTPException as exc:
            logger.warning("Ticket question candidate failed (%s): %s", candidate, exc.detail)
            continue
        successful_jql.append(candidate)
        for ticket in matches:
            dedupe_key = (str(ticket.get("instance") or ""), str(ticket.get("ticket") or ""))
            if dedupe_key not in gathered:
                gathered[dedupe_key] = ticket

    terms = _extract_question_terms(question)
    mentioned_keys = set(GENERIC_JIRA_KEY_REGEX.findall(question.upper()))
    scored_tickets = []
    for ticket in gathered.values():
        score = _ticket_relevance_score(ticket, question, terms, mentioned_keys)
        term_matches = _ticket_term_match_count(ticket, terms)
        key_mentioned = bool(ticket.get("ticket") and str(ticket.get("ticket")).upper() in mentioned_keys)
        scored_tickets.append((score, term_matches, key_mentioned, ticket.get("updated") or "", ticket))
    scored_tickets.sort(key=lambda item: (item[0], item[1], item[3]), reverse=True)
    tickets = [item[4] for item in scored_tickets]

    # If the broad search fan-out is too large, keep results that match multiple key terms.
    if len(tickets) > payload.limit * 2 and len(terms) >= 2:
        min_required = 2
        narrowed = [
            item[4]
            for item in scored_tickets
            if item[2] or item[1] >= min_required
        ]
        if len(narrowed) >= max(5, payload.limit // 2):
            tickets = narrowed
    shortlist_size = min(max(payload.limit * 3, 20), 80)
    shortlist = tickets[:shortlist_size]
    ranked_ids = await _llm_filter_and_rank_ticket_shortlist(question, shortlist, payload.limit)
    by_id = {f"{ticket.get('instance')}::{ticket.get('ticket')}": ticket for ticket in shortlist}
    reranked = [by_id[ticket_id] for ticket_id in ranked_ids if ticket_id in by_id]
    if not reranked:
        # Fail hard would be too punishing here; fallback to strongest heuristic ordering.
        reranked = shortlist[: payload.limit]

    limited = reranked[: payload.limit]
    primary_jql = successful_jql[0] if successful_jql else (attempted_jql[0] if attempted_jql else "")

    return TicketQuestionResponse(
        question=question,
        interpretation=resolved["interpretation"],
        jql=primary_jql,
        attempted_jql=attempted_jql,
        successful_jql=successful_jql,
        total_matches=len(reranked),
        limited_to=payload.limit,
        tickets=limited,
    )


@app.post("/ticket-assistant", response_model=TicketAssistantResponse)
async def ticket_assistant(payload: TicketAssistantRequest) -> TicketAssistantResponse:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")

    routed = await _infer_assistant_action(text)
    intent = routed["intent"]
    interpretation = routed.get("interpretation") or ""

    if intent == "create_linked_software_tickets":
        if payload.dry_run:
            approval_token, approval_ttl_seconds = await _mint_ticket_assistant_approval_token(intent=intent, text=text)
        else:
            approved = await _consume_ticket_assistant_approval_token(
                token=payload.approval_token,
                intent=intent,
                text=text,
            )
            if not approved:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Creation requires an approval token from a fresh dry run. "
                        "Run dry_run=true first, then submit dry_run=false with approval_token."
                    ),
                )

        create_block = routed.get("create") or {}
        implementations_raw = create_block.get("implementations")
        implementations: List[ImplementationTicketDraft] = []
        if isinstance(implementations_raw, list):
            for item in implementations_raw:
                if not isinstance(item, dict):
                    continue
                try:
                    implementations.append(ImplementationTicketDraft(**item))
                except Exception:
                    continue

        if not implementations:
            return TicketAssistantResponse(
                intent=intent,
                interpretation=interpretation,
                message=(
                    "I inferred create-linked-ticket intent but could not extract implementation drafts. "
                    "Please include one or more implementation bullet points."
                ),
                data={},
            )

        create_request = CreateLinkedSoftwareTicketsRequest(
            design_ticket_key=str(create_block.get("design_ticket_key") or ""),
            target_project=str(create_block.get("target_project") or "AP"),
            link_type=str(create_block.get("link_type") or "Relates"),
            implementations=implementations,
            dry_run=payload.dry_run,
        )
        create_result = await create_linked_software_tickets(create_request)
        response_data = _model_to_dict(create_result)
        if payload.dry_run:
            response_data["approval_token"] = approval_token
            response_data["approval_ttl_seconds"] = approval_ttl_seconds
            response_data["approval_required"] = True
        return TicketAssistantResponse(
            intent=intent,
            interpretation=interpretation,
            message="Created linked software tickets" if not payload.dry_run else "Dry run plan generated",
            data=response_data,
        )

    search_question = str((routed.get("search") or {}).get("question") or text).strip() or text
    search_result = await ticket_question(TicketQuestionRequest(question=search_question, limit=payload.limit))
    return TicketAssistantResponse(
        intent="search_tickets",
        interpretation=interpretation or search_result.interpretation,
        message="Search completed",
        data=_model_to_dict(search_result),
    )


@app.post("/ticket-actions/create-linked-software-tickets", response_model=CreateLinkedSoftwareTicketsResponse)
async def create_linked_software_tickets(payload: CreateLinkedSoftwareTicketsRequest) -> CreateLinkedSoftwareTicketsResponse:
    design_key = payload.design_ticket_key.strip().upper()
    target_project = payload.target_project.strip().upper()
    if not re.match(r"^[A-Z][A-Z0-9]+-\d+$", design_key):
        raise HTTPException(status_code=400, detail="design_ticket_key must look like ABC-123")
    if not re.match(r"^[A-Z][A-Z0-9]+$", target_project):
        raise HTTPException(status_code=400, detail="target_project must be a Jira project key")

    source_fields = ["summary", "description", "labels", "project"]
    cfg, source_issue = await _find_issue_and_config(design_key, source_fields)
    source_summary = str((source_issue.get("fields") or {}).get("summary") or "").strip()
    source_description = _adf_to_text((source_issue.get("fields") or {}).get("description")).strip()
    source_labels = (source_issue.get("fields") or {}).get("labels") or []
    base_url = cfg["base_url"].rstrip("/")

    created_tickets: List[Dict[str, Any]] = []
    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        for index, draft in enumerate(payload.implementations, start=1):
            description = draft.description.strip()
            if not description:
                description = (
                    f"Implementation ticket derived from design ticket {design_key}: {source_summary}\n\n"
                    f"Design ticket: {base_url}/browse/{design_key}\n\n"
                    f"{source_description}"
                ).strip()
            labels = _dedupe_labels(
                [
                    *(draft.labels or []),
                    *source_labels,
                    "implementation-ticket",
                    f"design-source-{design_key.lower()}",
                ]
            )
            issue_fields: Dict[str, Any] = {
                "project": {"key": target_project},
                "summary": draft.summary.strip(),
                "description": description,
                "issuetype": {"name": draft.issue_type.strip()},
                "labels": labels,
            }

            if payload.dry_run:
                created_tickets.append(
                    {
                        "index": index,
                        "summary": draft.summary.strip(),
                        "would_create_fields": issue_fields,
                        "would_link_to": design_key,
                        "link_type": payload.link_type,
                    }
                )
                continue

            create_url = f"{base_url}/rest/api/3/issue"
            create_resp = await client.post(
                create_url,
                auth=(cfg["email"], cfg["token"]),
                headers=headers,
                json={"fields": issue_fields},
            )
            if create_resp.status_code not in {200, 201}:
                raise HTTPException(status_code=create_resp.status_code, detail=create_resp.text)
            created_issue = create_resp.json()
            created_key = str(created_issue.get("key") or "").strip().upper()
            if not created_key:
                raise HTTPException(status_code=502, detail="Jira did not return a ticket key for created issue")

            link_url = f"{base_url}/rest/api/3/issueLink"
            link_payload = {
                "type": {"name": payload.link_type},
                "inwardIssue": {"key": design_key},
                "outwardIssue": {"key": created_key},
            }
            link_resp = await client.post(
                link_url,
                auth=(cfg["email"], cfg["token"]),
                headers=headers,
                json=link_payload,
            )
            if link_resp.status_code not in {200, 201}:
                raise HTTPException(status_code=link_resp.status_code, detail=link_resp.text)

            created_tickets.append(
                {
                    "index": index,
                    "key": created_key,
                    "summary": draft.summary.strip(),
                    "link": f"{base_url}/browse/{created_key}",
                    "linked_to": design_key,
                    "link_type": payload.link_type,
                }
            )

    return CreateLinkedSoftwareTicketsResponse(
        design_ticket_key=design_key,
        design_ticket_link=f"{base_url}/browse/{design_key}",
        target_project=target_project,
        dry_run=payload.dry_run,
        created_count=0 if payload.dry_run else len(created_tickets),
        tickets=created_tickets,
    )


async def fetch_jira_statuses(keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch Jira status and link for AP/PD issues in the palliativa instance."""
    if not keys:
        return {}
    unique_keys = sorted(set(keys))
    fields = ["status", "summary", "key", "labels", "fixVersions"]
    jql = f"key in ({', '.join(unique_keys)})"
    headers = {"Content-Type": "application/json"}
    cfg = next((item for item in configs if item.get("name") == "palliativa"), None)
    if not cfg:
        raise RuntimeError("Palliativa Jira config not found")
    async with httpx.AsyncClient() as client:
        search_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search/jql"
        resp = await client.post(search_url, auth=(cfg["email"], cfg["token"]), headers=headers, json={"jql": jql, "fields": fields})
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    results: Dict[str, Dict[str, str]] = {}
    for issue in resp.json().get("issues", []):
        key = issue.get("key")
        fields_data = issue.get("fields", {}) or {}
        status = (fields_data.get("status") or {}).get("name")
        summary = fields_data.get("summary") or ""
        if key:
            results[key] = {
                "key": key,
                "status": status or "",
                "summary": summary,
                "labels": fields_data.get("labels") or [],
                "fixVersions": [(item.get("name") or "") for item in (fields_data.get("fixVersions") or []) if item],
                "link": f"{cfg['base_url'].rstrip('/')}/browse/{key}",
            }
    return results


async def fetch_pr_details(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    owner: str,
    repo: str,
    number: int,
) -> Dict[str, str] | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
    resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    return {
        "number": data.get("number"),
        "title": data.get("title") or "",
        "link": data.get("html_url") or "",
    }


async def fetch_pr_commits(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    owner: str,
    repo: str,
    number: int,
) -> List[Dict[str, str]]:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/commits"
    resp = await client.get(url, headers=headers, params={"per_page": 100})
    if resp.status_code != 200:
        return []
    commits = []
    for item in resp.json():
        commit_info = item.get("commit") or {}
        author_info = commit_info.get("author") or {}
        raw_message = commit_info.get("message") or ""
        commits.append(
            {
                "sha": item.get("sha"),
                "message": raw_message.split("\n")[0],
                "author": author_info.get("name"),
                "date": author_info.get("date"),
                "link": item.get("html_url"),
            }
        )
    return commits


async def find_open_prs_for_ticket(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    owner: str,
    repo: str,
    base: str,
    ticket_key: str,
) -> List[Dict[str, Any]]:
    ticket_upper = ticket_key.strip().upper()
    if not ticket_upper:
        return []
    ticket_pattern = re.compile(rf"\b{re.escape(ticket_upper)}\b", re.IGNORECASE)
    pulls_url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    page = 1
    matching: List[Dict[str, Any]] = []
    while True:
        resp = await client.get(
            pulls_url,
            headers=headers,
            params={"state": "open", "base": base, "per_page": 100, "page": page},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        rows = resp.json() or []
        if not rows:
            break
        for pr in rows:
            haystack = " ".join(
                [
                    (pr.get("title") or ""),
                    (pr.get("body") or ""),
                    ((pr.get("head") or {}).get("ref") or ""),
                    ((pr.get("base") or {}).get("ref") or ""),
                ]
            )
            if ticket_pattern.search(haystack):
                matching.append(pr)
        page += 1
    return matching


@app.post("/github-merge-ticket-pr")
async def github_merge_ticket_pr(
    ticket: str,
    owner: str = "palliativa",
    repo: str = "monorepo",
    base: str = "codex/integration",
    method: Literal["merge", "squash", "rebase"] = "merge",
) -> Dict[str, Any]:
    ticket_key = ticket.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", ticket_key):
        raise HTTPException(status_code=400, detail=f"Invalid ticket key '{ticket}'")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient() as client:
        matching = await find_open_prs_for_ticket(
            client=client,
            headers=headers,
            owner=owner,
            repo=repo,
            base=base,
            ticket_key=ticket_key,
        )
        if not matching:
            raise HTTPException(
                status_code=404,
                detail=f"No open PR found for ticket '{ticket_key}' targeting '{base}' in {owner}/{repo}",
            )
        if len(matching) > 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"Multiple open PRs found for ticket '{ticket_key}' targeting '{base}'.",
                    "prs": [
                        {
                            "number": row.get("number"),
                            "title": row.get("title") or "",
                            "url": row.get("html_url") or "",
                            "head": ((row.get("head") or {}).get("ref") or ""),
                        }
                        for row in matching
                    ],
                },
            )

        pr = matching[0]
        pr_number = pr.get("number")
        if not isinstance(pr_number, int):
            raise HTTPException(status_code=500, detail="Matched PR did not include a valid PR number")
        merged_pr = await _merge_github_pr(client, headers, owner, repo, pr_number, method)

    if GITHUB_COMPARE_CACHE_TTL_SECONDS > 0:
        async with _github_compare_cache_lock:
            cache_keys = [key for key in _github_compare_cache.keys() if key[0] == owner and key[1] == repo]
            for cache_key in cache_keys:
                _github_compare_cache.pop(cache_key, None)

    return {
        "ticket": ticket_key,
        "owner": owner,
        "repo": repo,
        "base": base,
        "method": method,
        "pr": merged_pr,
    }


@app.post("/github-merge-pr")
async def github_merge_pr(
    pr_number: int,
    owner: str = "palliativa",
    repo: str = "monorepo",
    method: Literal["merge", "squash", "rebase"] = "merge",
) -> Dict[str, Any]:
    if pr_number <= 0:
        raise HTTPException(status_code=400, detail=f"Invalid PR number '{pr_number}'")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient() as client:
        merged_pr = await _merge_github_pr(client, headers, owner, repo, pr_number, method)

    if GITHUB_COMPARE_CACHE_TTL_SECONDS > 0:
        async with _github_compare_cache_lock:
            cache_keys = [key for key in _github_compare_cache.keys() if key[0] == owner and key[1] == repo]
            for cache_key in cache_keys:
                _github_compare_cache.pop(cache_key, None)

    return {
        "owner": owner,
        "repo": repo,
        "method": method,
        "pr": merged_pr,
    }


@app.get("/github-pr-queue")
async def github_pr_queue(
    owner: str = "palliativa",
    repo: str = "monorepo",
    base: str = "codex/integration",
) -> Dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    pulls_url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    page = 1
    open_prs: List[Dict[str, Any]] = []
    pr_mergeability: Dict[int, str] = {}
    timeout = httpx.Timeout(timeout=20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            try:
                resp = await client.get(
                    pulls_url,
                    headers=headers,
                    params={
                        "state": "open",
                        "base": base,
                        "sort": "updated",
                        "direction": "desc",
                        "per_page": 100,
                        "page": page,
                    },
                )
            except httpx.TimeoutException as exc:
                raise HTTPException(status_code=504, detail=f"GitHub API timeout while listing PRs: {exc!s}") from exc
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=f"GitHub API request error while listing PRs: {exc!s}") from exc
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            rows = resp.json() or []
            if not rows:
                break
            open_prs.extend(rows)
            page += 1

        async def fetch_mergeable_state(pr_number: int) -> tuple[int, str]:
            detail_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
            try:
                detail_resp = await client.get(detail_url, headers=headers)
            except httpx.TimeoutException as exc:
                raise HTTPException(
                    status_code=504,
                    detail=f"GitHub API timeout while fetching PR #{pr_number} mergeability: {exc!s}",
                ) from exc
            except httpx.RequestError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"GitHub API request error while fetching PR #{pr_number} mergeability: {exc!s}",
                ) from exc
            if detail_resp.status_code != 200:
                raise HTTPException(status_code=detail_resp.status_code, detail=detail_resp.text)
            detail = detail_resp.json() or {}
            return pr_number, str(detail.get("mergeable_state") or "").lower()

        pr_numbers = [int(pr.get("number")) for pr in open_prs if isinstance(pr.get("number"), int)]
        if pr_numbers:
            mergeability_rows = await asyncio.gather(*(fetch_mergeable_state(pr_number) for pr_number in pr_numbers))
            pr_mergeability = {pr_number: merge_state for pr_number, merge_state in mergeability_rows}

    def extract_ticket_keys_from_text(*parts: str) -> List[str]:
        seen: set[str] = set()
        ordered: List[str] = []
        combined = " ".join(parts)
        for key in GENERIC_JIRA_KEY_REGEX.findall(combined):
            normalized = key.upper()
            if normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
        return ordered

    all_ticket_keys: set[str] = set()
    for pr in open_prs:
        keys = extract_ticket_keys_from_text(
            pr.get("title") or "",
            pr.get("body") or "",
            ((pr.get("head") or {}).get("ref") or ""),
        )
        all_ticket_keys.update(keys)

    jira_lookup = await fetch_jira_statuses(sorted(all_ticket_keys))
    queue_rows: List[Dict[str, Any]] = []
    for pr in open_prs:
        pr_number = pr.get("number")
        mergeable_state = pr_mergeability.get(pr_number, str(pr.get("mergeable_state") or "").lower())
        has_merge_conflicts = mergeable_state == "dirty"
        ticket_keys = extract_ticket_keys_from_text(
            pr.get("title") or "",
            pr.get("body") or "",
            ((pr.get("head") or {}).get("ref") or ""),
        )
        queue_rows.append(
            {
                "number": pr_number,
                "title": pr.get("title") or "",
                "url": pr.get("html_url") or "",
                "state": pr.get("state") or "",
                "draft": bool(pr.get("draft")),
                "created_at": pr.get("created_at") or "",
                "updated_at": pr.get("updated_at") or "",
                "mergeable_state": mergeable_state,
                "has_merge_conflicts": has_merge_conflicts,
                "merge_warning": "Merge conflicts detected" if has_merge_conflicts else "",
                "head_ref": ((pr.get("head") or {}).get("ref") or ""),
                "head_sha": ((pr.get("head") or {}).get("sha") or ""),
                "author": ((pr.get("user") or {}).get("login") or ""),
                "tickets": [jira_lookup.get(key) or {"key": key, "status": "", "summary": "", "link": ""} for key in ticket_keys],
            }
        )

    return {
        "owner": owner,
        "repo": repo,
        "base": base,
        "total_open_prs": len(queue_rows),
        "prs": queue_rows,
    }


@app.get("/in-progress")
async def in_progress():
    """Aggregate Jira issues with status *In Progress* across instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'statusCategory = "In Progress"'
    flattened = await _search_jira(jql, fields)
    flattened.sort(key=lambda i: i.get("updated") or "")
    return flattened


@app.get("/open-issues-by-due")
async def open_issues_by_due():
    """Open (not-done) Jira issues sorted by due date (overdue first)."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "issuetype"]
    jql = "statusCategory != Done AND duedate IS NOT EMPTY"
    aggregated = await _search_jira(jql, fields)

    today = date.today().isoformat()

    # Only show issues with an explicit due date; the frontend "due" view
    # should not list everything that's merely open.
    aggregated = [item for item in aggregated if item.get("dueDate")]

    def sort_key(item: Dict[str, Any]):
        due = item.get("dueDate")
        return (due >= today, due)  # overdue first (False < True)

    aggregated.sort(key=sort_key)
    return aggregated


@app.get("/backlog")
async def backlog():
    """Aggregate Jira issues with status *To Do* from Palliativa instance."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "issuetype"]
    jql = 'project = "AP" AND statusCategory = "To Do" ORDER BY updated ASC'
    flattened = await _search_jira(jql, fields)
    return flattened


@app.get("/manager-meeting")
async def manager_meeting():
    """Tickets tagged for the manager meeting across Jira instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'statusCategory != Done AND labels = "ManagerMeeting"'
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "")
    return tickets


@app.get("/recently-updated")
async def recently_updated():
    """Tickets updated in the last 72h but not within the last 30 minutes."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype", "comment"]
    jql = "updated >= -72h AND updated <= -30m"
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "", reverse=True)
    return tickets


@app.get("/recently-closed")
async def recently_closed():
    """Done-category tickets resolved in the last 72 hours."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype", "comment"]
    jql = 'statusCategory = Done AND resolutiondate >= -72h'
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "", reverse=True)
    return tickets


@app.get("/codex-enrich")
async def codex_enrich():
    """Tickets tagged for Codex enrichment across Jira instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'labels in ("codex:enrich", "codex:enriched")'
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "", reverse=True)
    return tickets


@app.get("/codex-more-info")
async def codex_more_info():
    """Tickets tagged for additional Codex info across Jira instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'labels = "codex:more-info"'
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "", reverse=True)
    return tickets


@app.get("/codex-implemented")
async def codex_implemented():
    """Tickets tagged as implemented by Codex across Jira instances."""
    fields = ["summary", "project", "assignee", "updated", "duedate", "key", "status", "priority", "labels", "issuetype"]
    jql = 'labels = "codex:implemented"'
    tickets = await _search_jira(jql, fields)
    tickets.sort(key=lambda i: i.get("updated") or "", reverse=True)
    return tickets


# ---------------------------------------------------------------------------
# GitHub endpoints
# ---------------------------------------------------------------------------


@app.get("/github-branch-commits")
async def github_branch_commits(
    owner: str,
    repo: str,
    base: str = "latest-tag",
    head: str = "master",
    version: str | None = None,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Return commits on `head` that are not reachable from `base`."""
    cache_key = (owner, repo, base, head, version or "")
    if GITHUB_COMPARE_CACHE_TTL_SECONDS > 0 and not force_refresh:
        now = time.monotonic()
        async with _github_compare_cache_lock:
            cached = _github_compare_cache.get(cache_key)
            if cached:
                cached_at, cached_payload = cached
                if now - cached_at < GITHUB_COMPARE_CACHE_TTL_SECONDS:
                    logger.info("Serving cached GitHub compare response for %s/%s %s...%s", owner, repo, base, head)
                    return copy.deepcopy(cached_payload)
                _github_compare_cache.pop(cache_key, None)

    pr_number_regexes = [
        re.compile(r"merge pull request #(?P<num>\d+)", re.IGNORECASE),
        re.compile(r"\(#(?P<num>\d+)\)"),
    ]

    def extract_pr_numbers(text: str | None) -> List[int]:
        if not text:
            return []
        found: List[int] = []
        for regex in pr_number_regexes:
            for match in regex.finditer(text):
                try:
                    found.append(int(match.group("num")))
                except (TypeError, ValueError):
                    continue
        seen: set[int] = set()
        ordered: List[int] = []
        for num in found:
            if num not in seen:
                ordered.append(num)
                seen.add(num)
        return ordered

    def is_merge_commit(commit_payload: Dict[str, Any]) -> bool:
        parents = commit_payload.get("parents") or []
        return len(parents) > 1

    def extract_jira_keys(text: str | None) -> List[str]:
        if not text:
            return []
        matches = JIRA_KEY_REGEX.findall(text)
        seen: set[str] = set()
        ordered: List[str] = []
        for key in matches:
            if key not in seen:
                ordered.append(key)
                seen.add(key)
        return ordered

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async def fetch_repo_tags() -> List[Dict[str, str]]:
        tags_url = f"https://api.github.com/repos/{owner}/{repo}/tags"
        page = 1
        tag_rows: List[Dict[str, str]] = []
        async with httpx.AsyncClient() as client:
            while True:
                resp_t = await client.get(tags_url, headers=headers, params={"per_page": 100, "page": page})
                if resp_t.status_code != 200:
                    raise HTTPException(status_code=resp_t.status_code, detail=resp_t.text)
                values = resp_t.json()
                if not values:
                    break
                for tag in values:
                    tag_name = tag.get("name")
                    tag_commit = (tag.get("commit") or {}).get("sha")
                    if not tag_name or not tag_commit:
                        continue
                    tag_rows.append({"name": tag_name, "sha": tag_commit})
                page += 1
        return tag_rows

    async def fetch_registry_tags(registry_name: str) -> tuple[List[str], str]:
        if not digitalocean_token:
            return [], "DigitalOcean API token missing for container registry tags"

        headers_do = {
            "Authorization": f"Bearer {digitalocean_token}",
            "Content-Type": "application/json",
        }
        registry_base_url = f"https://api.digitalocean.com/v2/registry/{registry_name}"
        tag_rows: List[str] = []

        async def fetch_registry_repositories(client: httpx.AsyncClient) -> List[str]:
            page = 1
            repository_names: List[str] = []
            while True:
                resp = await client.get(
                    f"{registry_base_url}/repositories",
                    headers=headers_do,
                    params={"per_page": 100, "page": page},
                )
                if resp.status_code == 404:
                    return []
                if resp.status_code in (401, 403):
                    raise HTTPException(status_code=502, detail="DigitalOcean registry access denied for container registry tags")
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                payload = resp.json() or {}
                repositories = payload.get("repositories") or []
                if not repositories:
                    break
                for repository in repositories:
                    name = str(repository.get("name") or "").strip()
                    if name:
                        repository_names.append(name)
                links = (payload.get("links") or {}).get("pages") or {}
                if not links.get("next"):
                    break
                page += 1
            return repository_names

        async def fetch_repository_tags(client: httpx.AsyncClient, repository_name: str) -> List[str]:
            page = 1
            repository_tags: List[str] = []
            while True:
                resp = await client.get(
                    f"{registry_base_url}/repositories/{repository_name}/tags",
                    headers=headers_do,
                    params={"per_page": 100, "page": page},
                )
                if resp.status_code == 404:
                    return []
                if resp.status_code in (401, 403):
                    raise HTTPException(status_code=502, detail="DigitalOcean registry access denied for container registry tags")
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                payload = resp.json() or {}
                tags = payload.get("tags") or []
                if not tags:
                    break
                for tag_info in tags:
                    tag = str(tag_info.get("tag") or "").strip()
                    if tag:
                        repository_tags.append(tag)
                links = (payload.get("links") or {}).get("pages") or {}
                if not links.get("next"):
                    break
                page += 1
            return repository_tags

        async with httpx.AsyncClient() as client:
            repository_names = await fetch_registry_repositories(client)
            if not repository_names:
                return [], ""
            repository_tag_lists = await asyncio.gather(*(fetch_repository_tags(client, repository_name) for repository_name in repository_names))
        for repository_tags in repository_tag_lists:
            tag_rows.extend(repository_tags)
        seen: set[str] = set()
        ordered_tags: List[str] = []
        for tag in tag_rows:
            if tag in seen:
                continue
            seen.add(tag)
            ordered_tags.append(tag)
        return ordered_tags, ""

    def normalize_version_tag(requested_version: str, available_tags: List[str]) -> str | None:
        if requested_version in available_tags:
            return requested_version
        if requested_version.startswith("v"):
            alt = requested_version[1:]
            if alt in available_tags:
                return alt
        else:
            alt = f"v{requested_version}"
            if alt in available_tags:
                return alt
        return None

    resolved_base = base
    resolved_head = head
    latest_repo_tag: str | None = None
    requested_version = (version or "").strip()
    requested_release_version = requested_version if requested_version and requested_version.lower() != "next" else ""
    version_tag_found: bool | None = None
    version_unreleased_fallback = False
    resolved_release_tag = ""
    tags_for_resolution: List[Dict[str, str]] = []
    if base == "latest-tag" or (requested_version and requested_version.lower() != "next"):
        tags_for_resolution = await fetch_repo_tags()
        if not tags_for_resolution:
            raise HTTPException(status_code=400, detail="No tags found in repository")
        tag_names = [row["name"] for row in tags_for_resolution]
        semver_tag_names = [name for name in tag_names if re.match(r"^v?\d+\.\d+\.\d+$", name)]
        if requested_release_version:
            resolved_release_tag = normalize_version_tag(requested_release_version, tag_names) or ""
            if not resolved_release_tag:
                ordered_semver_tags = sorted(set(semver_tag_names), key=_semver_key)
                if not ordered_semver_tags:
                    raise HTTPException(
                        status_code=400,
                        detail="No semver tags found in repository; cannot resolve unreleased version range",
                    )
                version_tag_found = False
                version_unreleased_fallback = True
                resolved_base = ordered_semver_tags[-1]
                resolved_head = head
                latest_repo_tag = ordered_semver_tags[-1]
            elif resolved_release_tag not in semver_tag_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"Requested version tag '{resolved_release_tag}' is not a semver tag (expected vX.Y.Z or X.Y.Z)",
                )
            else:
                version_tag_found = True
                resolved_head = resolved_release_tag
                ordered_semver_tags = sorted(set(semver_tag_names), key=_semver_key)
                version_idx = ordered_semver_tags.index(resolved_head)
                if version_idx == 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No previous semver tag exists before '{resolved_head}'",
                    )
                resolved_base = ordered_semver_tags[version_idx - 1]
                latest_repo_tag = ordered_semver_tags[-1]

                master_compare_url = f"https://api.github.com/repos/{owner}/{repo}/compare/{resolved_head}...master"
                async with httpx.AsyncClient() as client:
                    master_compare_resp = await client.get(master_compare_url, headers=headers)
                if master_compare_resp.status_code != 200:
                    raise HTTPException(status_code=master_compare_resp.status_code, detail=master_compare_resp.text)
                master_compare = master_compare_resp.json() or {}
                if int(master_compare.get("behind_by") or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Version tag '{resolved_head}' is not on master ancestry (behind_by={master_compare.get('behind_by')})",
                    )
        elif base == "latest-tag":
            ordered_semver_tags = sorted(set(semver_tag_names), key=_semver_key)
            if not ordered_semver_tags:
                raise HTTPException(
                    status_code=400,
                    detail="No semver tags found in repository; cannot resolve base=latest-tag",
                )
            resolved_base = ordered_semver_tags[-1]
            latest_repo_tag = resolved_base

    compare_url = f"https://api.github.com/repos/{owner}/{repo}/compare/{resolved_base}...{resolved_head}"
    head_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{resolved_head}"
    async with httpx.AsyncClient() as client:
        compare_resp = await client.get(compare_url, headers=headers)
        head_resp = await client.get(head_url, headers=headers)
    if compare_resp.status_code != 200:
        raise HTTPException(status_code=compare_resp.status_code, detail=compare_resp.text)
    if head_resp.status_code != 200:
        raise HTTPException(status_code=head_resp.status_code, detail=head_resp.text)

    data = compare_resp.json()
    base_sha = (data.get("base_commit") or {}).get("sha")
    head_sha = head_resp.json().get("sha")
    commits_raw = data.get("commits", [])
    commit_shas = [commit.get("sha") for commit in commits_raw if commit.get("sha")]
    if head_sha and head_sha not in commit_shas:
        commit_shas.append(head_sha)

    jira_base_url = ""
    palliativa_cfg = next((item for item in configs if item.get("name") == "palliativa"), None)
    if palliativa_cfg:
        jira_base_url = palliativa_cfg.get("base_url", "").rstrip("/")

    jira_keys: List[str] = []
    for commit in commits_raw:
        raw_message = (commit.get("commit") or {}).get("message") or ""
        jira_keys.extend(extract_jira_keys(raw_message))
    jira_lookup = await fetch_jira_statuses(jira_keys)

    def build_jira_entries(keys: List[str]) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []
        for key in keys:
            entry = jira_lookup.get(key)
            if entry:
                entry_copy = dict(entry)
                entry_copy["sourceKey"] = key
                entries.append(entry_copy)
            else:
                link = f"{jira_base_url}/browse/{key}" if jira_base_url else ""
                entries.append({"key": key, "sourceKey": key, "status": "", "link": link})
        return entries

    tags_by_commit: Dict[str, List[str]] = {}
    latest_tag: str | None = None
    registry_tags: List[str] = []
    registry_tags_error = ""
    if commit_shas:
        tags_cache_key = (owner, repo)
        if GITHUB_TAGS_CACHE_TTL_SECONDS > 0 and not force_refresh:
            now = time.monotonic()
            async with _github_tags_cache_lock:
                cached_tags = _github_tags_cache.get(tags_cache_key)
                if cached_tags:
                    cached_at, cached_payload = cached_tags
                    if now - cached_at < GITHUB_TAGS_CACHE_TTL_SECONDS:
                        tags_by_commit = copy.deepcopy(cached_payload)
                    else:
                        _github_tags_cache.pop(tags_cache_key, None)
        if not tags_by_commit:
            fetched_map: Dict[str, List[str]] = {}
            tag_rows = tags_for_resolution if tags_for_resolution else await fetch_repo_tags()
            for tag in tag_rows:
                fetched_map.setdefault(tag["sha"], []).append(tag["name"])
            tags_by_commit = fetched_map
            if GITHUB_TAGS_CACHE_TTL_SECONDS > 0:
                async with _github_tags_cache_lock:
                    _github_tags_cache[tags_cache_key] = (time.monotonic(), copy.deepcopy(tags_by_commit))
        if head_sha:
            head_tags = tags_by_commit.get(head_sha, [])
            if head_tags:
                latest_tag = head_tags[0]
        registry_tags, registry_tags_error = await fetch_registry_tags(digitalocean_registry)

    commit_pr_numbers: Dict[str, List[int]] = {}
    unique_pr_numbers: set[int] = set()
    for commit in commits_raw:
        raw_message = (commit.get("commit") or {}).get("message") or ""
        sha = commit.get("sha")
        if not sha:
            continue
        pr_numbers = extract_pr_numbers(raw_message)
        commit_pr_numbers[sha] = pr_numbers
        unique_pr_numbers.update(pr_numbers)

    async def load_pr_data(
        pr_client: httpx.AsyncClient, pr_number: int
    ) -> Tuple[int, Dict[str, str] | None, List[Dict[str, str]]]:
        detail_key = (owner, repo, pr_number)
        commits_key = (owner, repo, pr_number)
        pr_detail: Dict[str, str] | None = None
        pr_commits: List[Dict[str, str]] = []

        detail_cached = False
        commits_cached = False
        if GITHUB_PR_CACHE_TTL_SECONDS > 0 and not force_refresh:
            now = time.monotonic()
            async with _github_pr_detail_cache_lock:
                cached_detail = _github_pr_detail_cache.get(detail_key)
                if cached_detail:
                    cached_at, payload = cached_detail
                    if now - cached_at < GITHUB_PR_CACHE_TTL_SECONDS:
                        pr_detail = copy.deepcopy(payload) if payload else None
                        detail_cached = True
                    else:
                        _github_pr_detail_cache.pop(detail_key, None)
            async with _github_pr_commits_cache_lock:
                cached_commits = _github_pr_commits_cache.get(commits_key)
                if cached_commits:
                    cached_at, payload = cached_commits
                    if now - cached_at < GITHUB_PR_CACHE_TTL_SECONDS:
                        pr_commits = copy.deepcopy(payload)
                        commits_cached = True
                    else:
                        _github_pr_commits_cache.pop(commits_key, None)

        tasks = []
        if not detail_cached:
            tasks.append(("detail", fetch_pr_details(pr_client, headers, owner, repo, pr_number)))
        if not commits_cached:
            tasks.append(("commits", fetch_pr_commits(pr_client, headers, owner, repo, pr_number)))
        if tasks:
            results = await asyncio.gather(*(task for _, task in tasks))
            for (kind, _), value in zip(tasks, results):
                if kind == "detail":
                    pr_detail = value
                elif kind == "commits":
                    pr_commits = value

        if GITHUB_PR_CACHE_TTL_SECONDS > 0:
            async with _github_pr_detail_cache_lock:
                _github_pr_detail_cache[detail_key] = (time.monotonic(), copy.deepcopy(pr_detail) if pr_detail else None)
            async with _github_pr_commits_cache_lock:
                _github_pr_commits_cache[commits_key] = (time.monotonic(), copy.deepcopy(pr_commits))

        return pr_number, pr_detail, pr_commits

    pr_cache: Dict[int, Dict[str, str]] = {}
    pr_commits_cache: Dict[int, List[Dict[str, str]]] = {}
    if unique_pr_numbers:
        async with httpx.AsyncClient() as pr_client:
            pr_results = await asyncio.gather(
                *(load_pr_data(pr_client, pr_number) for pr_number in sorted(unique_pr_numbers))
            )
        for pr_number, pr_detail, pr_commits in pr_results:
            if pr_detail:
                pr_cache[pr_number] = pr_detail
            pr_commits_cache[pr_number] = pr_commits

    commits: List[Dict[str, Any]] = []
    for commit in commits_raw:
        commit_info = commit.get("commit") or {}
        author_info = commit_info.get("author") or {}
        raw_message = commit_info.get("message") or ""
        sha = commit.get("sha")
        jira_entries = build_jira_entries(extract_jira_keys(raw_message))
        pr_numbers = commit_pr_numbers.get(sha or "", [])
        prs: List[Dict[str, str]] = [pr_cache[num] for num in pr_numbers if num in pr_cache]
        nested_commits: List[Dict[str, str]] = []
        if is_merge_commit(commit):
            for num in pr_numbers:
                if num in pr_commits_cache:
                    nested_commits = pr_commits_cache[num]
                    break
        commits.append(
            {
                "sha": sha,
                "date": author_info.get("date"),
                "author": author_info.get("name"),
                "message": raw_message.split("\n")[0],
                "link": commit.get("html_url"),
                "tags": tags_by_commit.get(sha, []),
                "jira": jira_entries,
                "prs": prs,
                "nested_commits": nested_commits,
                "is_merge_commit": is_merge_commit(commit),
            }
        )
    payload = {
        "owner": owner,
        "repo": repo,
        "base": resolved_base,
        "head": resolved_head,
        "requested_base": base,
        "requested_head": head,
        "requested_version": version or "",
        "force_refresh": force_refresh,
        "requested_release_version": requested_release_version,
        "version_tag_found": version_tag_found,
        "version_unreleased_fallback": version_unreleased_fallback,
        "resolved_release_tag": resolved_release_tag,
        "from_ref": resolved_base,
        "to_ref": resolved_head,
        "from_sha": base_sha,
        "to_sha": head_sha,
        "latest_repo_tag": latest_repo_tag,
        "compare_url": f"https://github.com/{owner}/{repo}/compare/{resolved_base}...{resolved_head}",
        "ahead_by": data.get("ahead_by"),
        "behind_by": data.get("behind_by"),
        "latest_tag": latest_tag,
        "registry_tags": registry_tags,
        "registry_tags_error": registry_tags_error,
        "total_commits": len(commits),
        "commits": commits,
    }
    if GITHUB_COMPARE_CACHE_TTL_SECONDS > 0:
        async with _github_compare_cache_lock:
            _github_compare_cache[cache_key] = (time.monotonic(), copy.deepcopy(payload))
    return payload


def _semver_key(version_name: str) -> Tuple[int, ...]:
    parts = [part for part in re.split(r"[^\d]+", version_name) if part]
    return tuple(int(part) for part in parts)


def _next_minor_version(version_name: str) -> str | None:
    parts = _semver_key(version_name)
    if len(parts) < 2:
        return None
    major, minor = parts[0], parts[1]
    return f"{major}.{minor + 1}.0"


def _strip_v_prefix(version_name: str) -> str:
    return version_name[1:] if version_name.startswith("v") else version_name


@app.get("/staging-tickets")
async def staging_tickets(project: str = "AP", version: str = "next") -> Dict[str, Any]:
    """Return staging tickets for a release version; version=next resolves the next unreleased Jira version."""
    cache_key = (project.upper(), version.strip().lower())
    if STAGING_TICKETS_CACHE_TTL_SECONDS > 0:
        now = time.monotonic()
        async with _staging_tickets_cache_lock:
            cached = _staging_tickets_cache.get(cache_key)
            if cached:
                cached_at, cached_payload = cached
                if now - cached_at < STAGING_TICKETS_CACHE_TTL_SECONDS:
                    return copy.deepcopy(cached_payload)
                _staging_tickets_cache.pop(cache_key, None)

    cfg = next((item for item in configs if item.get("name") == "palliativa"), None)
    if not cfg:
        raise RuntimeError("Palliativa Jira config not found")

    headers = {"Content-Type": "application/json"}
    base_url = cfg["base_url"].rstrip("/")
    search_url = f"{base_url}/rest/api/3/search/jql"
    versions_url = f"{base_url}/rest/api/3/project/{project}/versions"

    async with httpx.AsyncClient() as client:
        versions_resp = await client.get(
            versions_url,
            auth=(cfg["email"], cfg["token"]),
            headers=headers,
        )
    if versions_resp.status_code != 200:
        raise HTTPException(status_code=versions_resp.status_code, detail=versions_resp.text)

    versions_payload = versions_resp.json() or []
    unreleased_versions = [
        item.get("name")
        for item in versions_payload
        if item and item.get("name") and not item.get("released") and not item.get("archived")
    ]
    unreleased_versions = sorted(set(unreleased_versions), key=_semver_key)
    released_versions = sorted(
        {
            item.get("name")
            for item in versions_payload
            if item and item.get("name") and item.get("released") and not item.get("archived")
        },
        key=_semver_key,
        reverse=True,
    )
    available_versions = sorted(set(unreleased_versions + released_versions[:12]), key=_semver_key, reverse=True)

    headers_github = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    tags_url = "https://api.github.com/repos/palliativa/monorepo/tags"
    repo_semver_tags: List[str] = []
    async with httpx.AsyncClient() as client:
        page = 1
        while True:
            tags_resp = await client.get(tags_url, headers=headers_github, params={"per_page": 100, "page": page})
            if tags_resp.status_code != 200:
                raise HTTPException(status_code=tags_resp.status_code, detail=tags_resp.text)
            rows = tags_resp.json() or []
            if not rows:
                break
            for row in rows:
                name = row.get("name") or ""
                if re.fullmatch(r"v?\d+\.\d+\.\d+", name):
                    repo_semver_tags.append(name)
            page += 1

    if not repo_semver_tags:
        raise HTTPException(status_code=409, detail='Cannot resolve "next": no semver tags found in GitHub repository.')
    latest_repo_tag = sorted(set(repo_semver_tags), key=_semver_key)[-1]
    latest_prod_version = _strip_v_prefix(latest_repo_tag)
    computed_next_version = _next_minor_version(latest_prod_version)

    resolved_version = version
    if version == "next":
        if not computed_next_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    f'Cannot resolve version "next" from latest repository tag "{latest_repo_tag}" '
                    f'for project "{project}"'
                ),
            )
        if computed_next_version not in unreleased_versions:
            raise HTTPException(
                status_code=409,
                detail=(
                    f'Expected next version "{computed_next_version}" from latest repository tag "{latest_repo_tag}", '
                    f'but it is not an unreleased Jira version for project "{project}".'
                ),
            )
        resolved_version = computed_next_version

    jql = f'project = "{project}" AND fixVersion = "{resolved_version}" ORDER BY created DESC'
    fields = ["summary", "status", "labels", "issuetype", "fixVersions", "updated", "description", "comment"]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            search_url,
            auth=(cfg["email"], cfg["token"]),
            headers=headers,
            json={"jql": jql, "fields": fields},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    release_parent: Dict[str, Any] | None = None
    all_ticket_rows: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    for issue in resp.json().get("issues", []):
        fields_data = issue.get("fields", {}) or {}
        latest_comment = _latest_comment(fields_data) or {}
        description_text = _adf_to_text(fields_data.get("description")).strip()
        if len(description_text) > 600:
            description_text = description_text[:597] + "..."
        ticket_data = {
            "ticket": issue.get("key"),
            "title": fields_data.get("summary") or "",
            "statusName": (fields_data.get("status") or {}).get("name") or "",
            "issuetype": (fields_data.get("issuetype") or {}).get("name") or "",
            "labels": fields_data.get("labels") or [],
            "fixVersions": [(item.get("name") or "") for item in (fields_data.get("fixVersions") or []) if item],
            "updated": fields_data.get("updated"),
            "descriptionText": description_text,
            "latestComment": latest_comment,
            "audienceSummary": "",
            "link": f"{base_url}/browse/{issue.get('key')}",
        }
        if ("release-ticket" in ticket_data["labels"] or "release-train" in ticket_data["labels"]) and release_parent is None:
            release_parent = ticket_data
            all_ticket_rows.append(ticket_data)
        else:
            results.append(ticket_data)
            all_ticket_rows.append(ticket_data)
    if release_parent is None:
        release_parent = next(
            (
                item
                for item in results
                if "release-ticket" in item.get("labels", []) or "release-train" in item.get("labels", [])
            ),
            None,
        )
        if release_parent:
            results = [item for item in results if item.get("ticket") != release_parent.get("ticket")]

    async def summarize_ticket_row(ticket_row: Dict[str, Any]) -> None:
        ticket_row["audienceSummary"] = await _generate_ticket_audience_summary(
            ticket_key=str(ticket_row.get("ticket") or ""),
            updated=str(ticket_row.get("updated") or ""),
            summary=str(ticket_row.get("title") or ""),
            status=str(ticket_row.get("statusName") or ""),
            description=str(ticket_row.get("descriptionText") or ""),
            latest_comment=str((ticket_row.get("latestComment") or {}).get("body") or ""),
        )

    if all_ticket_rows:
        await asyncio.gather(*(summarize_ticket_row(ticket_row) for ticket_row in all_ticket_rows))

    payload = {
        "project": project,
        "requested_version": version,
        "resolved_version": resolved_version,
        "available_versions": available_versions,
        "next_version": computed_next_version,
        "release_parent": release_parent,
        "tickets": results,
    }
    if STAGING_TICKETS_CACHE_TTL_SECONDS > 0:
        async with _staging_tickets_cache_lock:
            _staging_tickets_cache[cache_key] = (time.monotonic(), copy.deepcopy(payload))
    return payload


@app.post("/staging-backfill-fix-version")
async def staging_backfill_fix_version(
    version: str,
    project: str = "AP",
    owner: str = "palliativa",
    repo: str = "monorepo",
    base: str = "master",
    head: str = "codex/integration",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Backfill Jira Fix Version for tickets present in codex/integration commits but missing the given release version."""
    project_prefix = f"{project.upper()}-"
    compare_data = await github_branch_commits(owner=owner, repo=repo, base=base, head=head)
    branch_keys: set[str] = set()
    for commit in compare_data.get("commits", []):
        for jira_item in commit.get("jira", []):
            key = jira_item.get("key")
            if key and key.upper().startswith(project_prefix):
                branch_keys.add(key.upper())
    if not branch_keys:
        return {"project": project.upper(), "version": version, "dry_run": dry_run, "candidates": [], "updated": []}

    cfg = next((item for item in configs if item.get("name") == "palliativa"), None)
    if not cfg:
        raise RuntimeError("Palliativa Jira config not found")

    search_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/search/jql"
    headers = {"Content-Type": "application/json"}
    jql = f"key in ({', '.join(sorted(branch_keys))})"
    async with httpx.AsyncClient() as client:
        search_resp = await client.post(
            search_url,
            auth=(cfg["email"], cfg["token"]),
            headers=headers,
            json={"jql": jql, "fields": ["key", "fixVersions"]},
        )
    if search_resp.status_code != 200:
        raise HTTPException(status_code=search_resp.status_code, detail=search_resp.text)

    missing: List[str] = []
    for issue in search_resp.json().get("issues", []):
        key = issue.get("key")
        fix_versions = [(item.get("name") or "") for item in ((issue.get("fields") or {}).get("fixVersions") or []) if item]
        if key and version not in fix_versions:
            missing.append(key)

    if dry_run or not missing:
        return {"project": project.upper(), "version": version, "dry_run": dry_run, "candidates": sorted(branch_keys), "updated": []}

    updated: List[str] = []
    async with httpx.AsyncClient() as client:
        for key in missing:
            issue_url = f"{cfg['base_url'].rstrip('/')}/rest/api/3/issue/{key}"
            update_resp = await client.put(
                issue_url,
                auth=(cfg["email"], cfg["token"]),
                headers=headers,
                json={"update": {"fixVersions": [{"add": {"name": version}}]}},
            )
            if update_resp.status_code != 204:
                raise HTTPException(status_code=update_resp.status_code, detail=update_resp.text)
            updated.append(key)

    return {
        "project": project.upper(),
        "version": version,
        "dry_run": dry_run,
        "candidates": sorted(branch_keys),
        "updated": sorted(updated),
    }
