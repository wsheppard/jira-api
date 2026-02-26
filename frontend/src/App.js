import React, { useState, useEffect, useCallback, useRef } from 'react';
import TicketsList from './TicketsList';
import PipelineDashboard from './PipelineDashboard';
import './App.css';

const API_BASE_URL = 'https://jira.api.jjrsoftware.co.uk';
const STAGING_VIEW_ID = 'codexIntegrationCommits';

const VIEW_CONFIG = {
  open: { label: 'Open Tickets by Due Date', endpoint: 'open-issues-by-due', type: 'tickets' },
  inProgress: { label: 'In Progress Tickets', endpoint: 'in-progress', type: 'tickets' },
  backlog: { label: 'Backlog', endpoint: 'backlog', type: 'tickets' },
  managerMeeting: { label: 'Manager Meeting', endpoint: 'manager-meeting', type: 'tickets' },
  recentActivity: { label: 'Updated Last 72h (excl. last 30m)', endpoint: 'recently-updated', type: 'tickets' },
  codexEnrich: { label: 'Codex Enrich / Enriched', endpoint: 'codex-enrich', type: 'tickets' },
  codexMoreInfo: { label: 'Codex More Info', endpoint: 'codex-more-info', type: 'tickets' },
  codexImplemented: { label: 'Codex Implemented', endpoint: 'codex-implemented', type: 'tickets' },
  codexIntegrationCommits: {
    label: 'Staging View',
    endpoint: 'github-branch-commits?owner=palliativa&repo=monorepo&base=master&head=codex/integration',
    type: 'githubCommits',
  },
  pipeline: { label: 'Pipeline Dashboard', endpoint: 'pipeline-dashboard', type: 'pipeline' },
};

const VIEW_ORDER = [
  'open',
  'inProgress',
  'backlog',
  'managerMeeting',
  'recentActivity',
  'codexEnrich',
  'codexMoreInfo',
  'codexImplemented',
  'codexIntegrationCommits',
  'pipeline',
];
const DEFAULT_VIEW = 'open';

const pathForView = (viewId) => (viewId === DEFAULT_VIEW ? '/' : `/view/${viewId}`);
const stagingVersionFromSearch = (search) => {
  const params = new URLSearchParams(search || '');
  return params.get('version') || 'next';
};
const urlForView = (viewId, stagingVersion = 'next') => {
  const path = pathForView(viewId);
  if (viewId !== STAGING_VIEW_ID) {
    return path;
  }
  const params = new URLSearchParams();
  params.set('version', stagingVersion || 'next');
  return `${path}?${params.toString()}`;
};

const normalizePath = (path) => {
  if (!path) {
    return '/';
  }
  if (path.length > 1 && path.endsWith('/')) {
    return path.replace(/\/+$/, '');
  }
  return path;
};

const viewFromLocation = (path) => {
  const normalized = normalizePath(path);
  if (normalized === '/' || normalized === '') {
    return DEFAULT_VIEW;
  }
  const match = normalized.match(/^\/view\/([^/]+)$/);
  if (match) {
    const candidate = match[1];
    if (VIEW_CONFIG[candidate]) {
      return candidate;
    }
  }
  return DEFAULT_VIEW;
};

function App() {
  const deriveInitialView = () => {
    if (typeof window === 'undefined') {
      return DEFAULT_VIEW;
    }
    return viewFromLocation(window.location.pathname);
  };

  const [activeView, setActiveView] = useState(deriveInitialView);
  const [stagingVersion, setStagingVersion] = useState(
    typeof window === 'undefined' ? 'next' : stagingVersionFromSearch(window.location.search),
  );
  const [ticketsByView, setTicketsByView] = useState({
    open: [],
    inProgress: [],
    backlog: [],
    managerMeeting: [],
    recentActivity: [],
    codexEnrich: [],
    codexMoreInfo: [],
    codexImplemented: [],
  });
  const [githubCommits, setGithubCommits] = useState([]);
  const [githubCompare, setGithubCompare] = useState(null);
  const [stagingTickets, setStagingTickets] = useState([]);
  const [stagingReleaseParent, setStagingReleaseParent] = useState(null);
  const [stagingAvailableVersions, setStagingAvailableVersions] = useState([]);
  const [stagingResolvedVersion, setStagingResolvedVersion] = useState('');
  const [stagingNextVersion, setStagingNextVersion] = useState('');
  const [pipelineData, setPipelineData] = useState({});
  const [pipelineCategories, setPipelineCategories] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
const [nextPollIn, setNextPollIn] = useState(30);
  const pendingRequests = useRef(0);
  const groupOrderRef = useRef(new Map());
  const hasSyncedInitialPath = useRef(false);
  const activeConfig = VIEW_CONFIG[activeView];

  const markRequestStart = useCallback(() => {
    pendingRequests.current += 1;
    setIsLoading(true);
  }, []);

  const markRequestEnd = useCallback(() => {
    pendingRequests.current = Math.max(pendingRequests.current - 1, 0);
    if (pendingRequests.current === 0) {
      setIsLoading(false);
    }
  }, []);

  const summarizeErrorDetail = useCallback((detail) => {
    if (detail == null) {
      return '';
    }
    if (typeof detail === 'string') {
      const trimmed = detail.trim();
      if (!trimmed) {
        return '';
      }
      try {
        return summarizeErrorDetail(JSON.parse(trimmed));
      } catch (parseError) {
        return trimmed;
      }
    }
    if (Array.isArray(detail)) {
      return detail.map((entry) => summarizeErrorDetail(entry)).filter(Boolean).join('; ');
    }
    if (typeof detail === 'object') {
      if (Array.isArray(detail.errorMessages) && detail.errorMessages.length > 0) {
        return detail.errorMessages.join('; ');
      }
      if (detail.errors && typeof detail.errors === 'object') {
        const values = Object.values(detail.errors).map((entry) => summarizeErrorDetail(entry)).filter(Boolean);
        if (values.length > 0) {
          return values.join('; ');
        }
      }
      if (typeof detail.message === 'string') {
        return detail.message;
      }
      try {
        return JSON.stringify(detail);
      } catch (stringifyError) {
        return String(detail);
      }
    }
    return String(detail);
  }, []);

  const fetchJson = useCallback(async (endpoint) => {
    let response;
    try {
      response = await fetch(`${API_BASE_URL}/${endpoint}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unexpected error while fetching data.';
      const reason = message === 'Failed to fetch'
        ? 'Network error or CORS issue while calling the API.'
        : message;
      throw new Error(`Failed to fetch ${endpoint}: ${reason}`);
    }
    if (!response.ok) {
      let detail = '';
      try {
        const body = await response.json();
        detail = summarizeErrorDetail(body?.detail ?? body);
      } catch (jsonError) {
        try {
          const text = await response.text();
          detail = summarizeErrorDetail(text);
        } catch (textError) {
          detail = '';
        }
      }
      const suffix = detail ? ` - ${detail}` : '';
      throw new Error(`Request failed: ${response.status} ${response.statusText}${suffix}`);
    }
    return response.json();
  }, [summarizeErrorDetail]);

  const fetchViewData = useCallback(async (view) => {
    const config = VIEW_CONFIG[view];
    if (!config) {
      return;
    }
    setNextPollIn(30);

    markRequestStart();
    setErrorMessage('');
    try {
      if (config.type === 'pipeline') {
        const data = await fetchJson(config.endpoint);
        setPipelineData(data);
        const repos = Object.keys(data);
        if (repos.length > 0) {
          setPipelineCategories(Object.keys(data[repos[0]]));
        } else {
          setPipelineCategories([]);
        }
      } else if (config.type === 'githubCommits') {
        const stagingData = await fetchJson(`staging-tickets?project=AP&version=${encodeURIComponent(stagingVersion || 'next')}`);
        setStagingTickets(Array.isArray(stagingData?.tickets) ? stagingData.tickets : []);
        setStagingReleaseParent(stagingData?.release_parent ?? null);
        setStagingAvailableVersions(Array.isArray(stagingData?.available_versions) ? stagingData.available_versions : []);
        setStagingResolvedVersion(stagingData?.resolved_version || '');
        setStagingNextVersion(stagingData?.next_version || '');
        const selectedVersion = stagingData?.resolved_version || '';
        const nextVersion = stagingData?.next_version || '';
        const shouldShowLiveCommits = Boolean(selectedVersion && nextVersion && selectedVersion === nextVersion);
        if (shouldShowLiveCommits) {
          const data = await fetchJson(config.endpoint);
          setGithubCommits(Array.isArray(data?.commits) ? data.commits : []);
          setGithubCompare(data ?? null);
        } else {
          setGithubCommits([]);
          setGithubCompare(null);
        }
      } else {
        const data = await fetchJson(config.endpoint);
        setTicketsByView((prev) => ({
          ...prev,
          [view]: data,
        }));
      }
    } catch (error) {
      console.error(`Failed to load ${config.label}:`, error);
      const message = error instanceof Error ? error.message : 'Unexpected error while fetching data.';
      setErrorMessage(message);
    } finally {
      markRequestEnd();
    }
  }, [fetchJson, markRequestEnd, markRequestStart, stagingVersion]);

  const hideOffcanvas = useCallback(() => {
    const offcanvasElement = document.getElementById('viewSelector');
    const bootstrapGlobal = window.bootstrap;
    if (!offcanvasElement || !bootstrapGlobal || !bootstrapGlobal.Offcanvas) {
      return;
    }
    const instance = bootstrapGlobal.Offcanvas.getInstance(offcanvasElement)
      || new bootstrapGlobal.Offcanvas(offcanvasElement);
    instance.hide();
  }, []);

  const handleSelectView = useCallback((viewId) => {
    if (!VIEW_CONFIG[viewId]) {
      return;
    }
    if (viewId === activeView) {
      fetchViewData(viewId);
      hideOffcanvas();
      return;
    }
    if (typeof window !== 'undefined') {
      window.history.pushState({ view: viewId }, '', urlForView(viewId, stagingVersion));
    }
    setActiveView(viewId);
    hideOffcanvas();
  }, [activeView, fetchViewData, hideOffcanvas, stagingVersion]);

  useEffect(() => {
    const onPopState = () => {
      const nextView = viewFromLocation(window.location.pathname);
      setStagingVersion(stagingVersionFromSearch(window.location.search));
      setActiveView((prev) => (prev === nextView ? prev : nextView));
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  useEffect(() => {
    if (typeof window !== 'undefined' && !hasSyncedInitialPath.current) {
      const desiredUrl = urlForView(activeView, stagingVersion);
      const currentUrl = `${window.location.pathname}${window.location.search || ''}`;
      if (currentUrl !== desiredUrl) {
        window.history.replaceState({ view: activeView }, '', desiredUrl);
      }
      hasSyncedInitialPath.current = true;
    }
    fetchViewData(activeView);
  }, [activeView, fetchViewData, stagingVersion]);

  const handleStagingVersionChange = useCallback((nextVersion) => {
    const value = nextVersion || 'next';
    setStagingVersion(value);
    if (typeof window !== 'undefined' && activeView === STAGING_VIEW_ID) {
      window.history.pushState({ view: STAGING_VIEW_ID }, '', urlForView(STAGING_VIEW_ID, value));
    }
  }, [activeView]);

  useEffect(() => {
    const baseTitle = 'JJR Jira Dashboard';
    if (activeConfig) {
      document.title = `${activeConfig.label} • ${baseTitle}`;
    } else {
      document.title = baseTitle;
    }
  }, [activeConfig]);

  useEffect(() => {
    const interval = setInterval(() => {
      fetchViewData(activeView);
    }, 30000);
    return () => clearInterval(interval);
  }, [activeView, fetchViewData]);
  useEffect(() => {
    const interval = setInterval(() => {
      setNextPollIn(prev => Math.max(0, prev - 1));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const buildCommitGroups = () => {
    const groups = new Map();
    const noJiraKey = 'NO-JIRA';

    const addToGroup = (groupKey, meta, commit) => {
      if (!groups.has(groupKey)) {
        groups.set(groupKey, {
          key: groupKey,
          title: meta?.summary || '',
          status: meta?.status || '',
          labels: Array.isArray(meta?.labels) ? meta.labels : [],
          link: meta?.link || '',
          commits: [],
        });
      }
      groups.get(groupKey).commits.push(commit);
    };

    const addCommit = (commit) => {
      const jiraItems = Array.isArray(commit?.jira) ? commit.jira : [];
      if (jiraItems.length === 0) {
        addToGroup(noJiraKey, null, commit);
        return;
      }
      jiraItems.forEach((jiraItem) => addToGroup(jiraItem.key, jiraItem, commit));
    };

    githubCommits.forEach(addCommit);

    const groupList = Array.from(groups.values());
    groupList.forEach((group) => {
      group.commits.sort((a, b) => (b?.date || '').localeCompare(a?.date || ''));
    });
    const nestedCommitShas = new Set();
    groupList.forEach((group) => {
      group.commits.forEach((commit) => {
        if (Array.isArray(commit?.nested_commits)) {
          commit.nested_commits.forEach((nested) => {
            if (nested?.sha) {
              nestedCommitShas.add(nested.sha);
            }
          });
        }
      });
    });
    groupList.forEach((group) => {
      group.commits = group.commits.filter((commit) => !nestedCommitShas.has(commit.sha));
    });
    const orderMap = groupOrderRef.current;
    let nextIndex = orderMap.size;
    groupList.forEach((group) => {
      if (!orderMap.has(group.key)) {
        orderMap.set(group.key, nextIndex);
        nextIndex += 1;
      }
    });
    groupList.sort((a, b) => {
      if (a.key === noJiraKey) return 1;
      if (b.key === noJiraKey) return -1;
      return (orderMap.get(a.key) ?? 0) - (orderMap.get(b.key) ?? 0);
    });
    return groupList;
  };

  const renderPrLinks = (prs) => {
    if (!Array.isArray(prs) || prs.length === 0) {
      return null;
    }
    return prs.map((pr, index) => (
      <span key={`${pr.number}-${index}`} className="me-2">
        {pr.link ? (
          <a href={pr.link} target="_blank" rel="noopener noreferrer">
            PR #{pr.number} — {pr.title || 'Untitled'}
          </a>
        ) : (
          `PR #${pr.number} — ${pr.title || 'Untitled'}`
        )}
      </span>
    ));
  };

  return (
    <div className="container-fluid p-4">
      <div className="d-flex align-items-center justify-content-between mb-3">
        <div className="d-flex align-items-center gap-3">
          <h1 className="mb-0">{activeConfig?.label ?? 'Dashboard'}</h1>
          {isLoading && (
            <div className="spinner-border spinner-border-sm text-primary" role="status">
              <span className="visually-hidden">Loading...</span>
            </div>
          )}
        </div>
          {nextPollIn > 0 && <small className="text-muted">Next update in {nextPollIn}s</small>}
        <button
          className="btn btn-outline-primary"
          type="button"
          data-bs-toggle="offcanvas"
          data-bs-target="#viewSelector"
          aria-controls="viewSelector"
        >
          Choose View
        </button>
      </div>

      {errorMessage && (
        <div className="alert alert-danger" role="alert">
          {errorMessage}
        </div>
      )}

      <div
        className="offcanvas offcanvas-start"
        tabIndex="-1"
        id="viewSelector"
        aria-labelledby="viewSelectorLabel"
      >
        <div className="offcanvas-header">
          <h5 className="offcanvas-title" id="viewSelectorLabel">Select View</h5>
          <button type="button" className="btn-close" data-bs-dismiss="offcanvas" aria-label="Close"></button>
        </div>
        <div className="offcanvas-body">
          <div className="list-group">
            {VIEW_ORDER.map((viewId) => {
              const config = VIEW_CONFIG[viewId];
              return (
                <button
                  key={viewId}
                  type="button"
                  className={`list-group-item list-group-item-action ${activeView === viewId ? 'active' : ''}`}
                  data-bs-dismiss="offcanvas"
                  onClick={() => handleSelectView(viewId)}
                >
                  {config.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {activeConfig?.type === 'pipeline' ? (
        pipelineCategories.length === 0 && !isLoading ? (
          <p className="text-muted fst-italic">No pipeline data available.</p>
        ) : (
          <PipelineDashboard data={pipelineData} categories={pipelineCategories} />
        )
      ) : activeConfig?.type === 'githubCommits' ? (
        <div className="card shadow-sm">
          <div className="card-body">
              <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
                <div>
                  <div className="fw-semibold d-flex align-items-center gap-2">
                    <span>palliativa/monorepo</span>
                    {githubCompare?.latest_tag && (
                      <span className="badge text-bg-secondary">Latest tag: {githubCompare.latest_tag}</span>
                    )}
                  </div>
                </div>
                <div className="d-flex align-items-center gap-2">
                  <a
                    href="https://github.com/palliativa/monorepo/pulls?q=is%3Aopen+is%3Apr+base%3Acodex%2Fintegration"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn btn-sm btn-outline-primary"
                  >
                    Open PRs to codex/integration
                  </a>
                  {githubCompare && (
                    <span className="badge text-bg-primary">{githubCompare.total_commits ?? githubCommits.length} commits</span>
                  )}
                </div>
              </div>
              <div className="mb-3 border rounded p-2">
                <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
                  <span className="fw-semibold">Release Scope</span>
                  <label htmlFor="stagingVersionSelect" className="small text-muted">Version</label>
                  <select
                    id="stagingVersionSelect"
                    className="form-select form-select-sm"
                    style={{ width: 'auto' }}
                    value={stagingVersion}
                    onChange={(event) => handleStagingVersionChange(event.target.value)}
                  >
                    <option value="next">{stagingNextVersion ? `next (${stagingNextVersion})` : 'next'}</option>
                    {stagingVersion !== 'next' && !stagingAvailableVersions.includes(stagingVersion) && (
                      <option value={stagingVersion}>{stagingVersion}</option>
                    )}
                    {stagingAvailableVersions.filter((versionName) => versionName !== stagingNextVersion).map((versionName) => (
                      <option key={versionName} value={versionName}>{versionName}</option>
                    ))}
                  </select>
                  {stagingResolvedVersion && (
                    <span className="badge text-bg-light border">Resolved: {stagingResolvedVersion}</span>
                  )}
                </div>
                {stagingReleaseParent ? (
                  <div className="border rounded p-2 bg-warning-subtle">
                    <div className="d-flex flex-wrap align-items-center gap-2">
                      <a href={stagingReleaseParent.link} target="_blank" rel="noopener noreferrer" className="fw-semibold">
                        {stagingReleaseParent.ticket}
                      </a>
                      <span>{stagingReleaseParent.title}</span>
                      {stagingReleaseParent.statusName && <span className="badge text-bg-secondary">{stagingReleaseParent.statusName}</span>}
                      {Array.isArray(stagingReleaseParent.fixVersions) && stagingReleaseParent.fixVersions.length > 0 && (
                        <span className="badge text-bg-light border">{stagingReleaseParent.fixVersions.join(', ')}</span>
                      )}
                      {Array.isArray(stagingReleaseParent.labels) && stagingReleaseParent.labels.map((label) => (
                        <span key={`${stagingReleaseParent.ticket}-${label}`} className="badge staging-label-badge">
                          {label}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="text-muted small">No release-train parent ticket found for this version.</div>
                )}
              </div>
              {stagingTickets.length > 0 && (
                <div className="mb-3">
                  <div className="fw-semibold mb-2">Staging Tickets</div>
                  <div className="d-flex flex-column gap-2">
                    {stagingTickets.map((ticket) => (
                      <div key={ticket.ticket} className="border rounded p-2">
                        <div className="d-flex flex-wrap align-items-center gap-2">
                          <a href={ticket.link} target="_blank" rel="noopener noreferrer" className="fw-semibold">
                            {ticket.ticket}
                          </a>
                          <span className="text-muted">{ticket.title}</span>
                          {ticket.statusName && <span className="badge text-bg-secondary">{ticket.statusName}</span>}
                          {Array.isArray(ticket.fixVersions) && ticket.fixVersions.length > 0 && (
                            <span className="badge text-bg-light border">{ticket.fixVersions.join(', ')}</span>
                          )}
                          {Array.isArray(ticket.labels) && ticket.labels.map((label) => (
                            <span key={`${ticket.ticket}-${label}`} className="badge staging-label-badge">
                              {label}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {stagingResolvedVersion && stagingNextVersion && stagingResolvedVersion !== stagingNextVersion ? (
                <div className="alert alert-secondary mb-0">
                  Commit timeline is only shown for the next release ({stagingNextVersion}).
                </div>
              ) : githubCommits.length === 0 && !isLoading ? (
                <p className="text-muted fst-italic mb-0">No commits found for this branch comparison.</p>
              ) : (
                <div className="row g-3">
                  {buildCommitGroups().map((group) => (
                  <div className="col-12 col-xl-6" key={group.key}>
                    <div className="card h-100">
                      <div className="card-header">
                        <div className="d-flex flex-wrap align-items-center gap-2">
                          {group.key === 'NO-JIRA' ? (
                            <span className="fw-semibold">No Jira</span>
                          ) : (
                            <div className="d-flex align-items-center gap-2 flex-shrink-0">
                              {group.link ? (
                                <a href={group.link} target="_blank" rel="noopener noreferrer" className="fw-semibold">
                                  {group.key}
                                </a>
                              ) : (
                                <span className="fw-semibold">{group.key}</span>
                              )}
                            </div>
                          )}
                        {group.key !== 'NO-JIRA' && group.title && (
                          <div className="text-muted flex-grow-1 staging-group-title" title={group.title}>
                            {group.title}
                          </div>
                        )}
                          {group.status && <span className="badge text-bg-secondary">{group.status}</span>}
                          <span className="badge text-bg-light border">{group.commits.length} commits</span>
                        </div>
                        {Array.isArray(group.labels) && group.labels.length > 0 && (
                          <div className="d-flex flex-wrap gap-1 mt-2">
                            {group.labels.map((label) => (
                              <span key={`${group.key}-label-${label}`} className="badge staging-label-badge">
                                {label}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <ul className="list-group list-group-flush">
                        {group.commits.map((commit) => {
                          const hasNested = Array.isArray(commit.nested_commits) && commit.nested_commits.length > 0;
                          return (
                            <li
                              key={`${group.key}-${commit.sha}`}
                              className="list-group-item"
                            >
                              <div className="commit-tree">
                                <div className="commit-parent-row">
                                  <div className={`connector-lane ${hasNested ? 'has-children' : ''}`} aria-hidden="true">
                                    <span className="connector-dot"></span>
                                  </div>
                                  <div className="commit-node">
                                    <div className="commit-hash-message">
                                      <span className="commit-hash">
                                        {commit.link ? (
                                          <a href={commit.link} target="_blank" rel="noopener noreferrer">
                                            {commit.sha?.slice(0, 7) ?? 'unknown'}
                                          </a>
                                        ) : (
                                          commit.sha?.slice(0, 7) ?? 'unknown'
                                        )}
                                      </span>
                                      <span className="commit-message-text">{commit.message || 'No message'}</span>
                                    </div>
                                    <div className="commit-meta-row">
                                      {Array.isArray(commit.tags) && commit.tags.length > 0 && (
                                        <span>
                                          {commit.tags.map((tag) => (
                                            <span key={tag} className="badge text-bg-secondary me-1">
                                              {tag}
                                            </span>
                                          ))}
                                        </span>
                                      )}
                                    </div>
                                    <div className="text-muted small">
                                      {commit.author || 'Unknown'} · {commit.date ? new Date(commit.date).toLocaleString() : 'Unknown'}
                                    </div>
                                    {renderPrLinks(commit.prs) && (
                                      <div className="small mt-1">PRs: {renderPrLinks(commit.prs)}</div>
                                    )}
                                  </div>
                                </div>
                                {hasNested && (
                                  <ul className="list-group list-group-flush mt-2 nested-commit-list">
                                    {commit.nested_commits.map((nested, nestedIndex) => (
                                      <li
                                        key={`${commit.sha}-${nested.sha}`}
                                        className={`list-group-item nested-commit-item ${nestedIndex === commit.nested_commits.length - 1 ? 'is-last' : ''}`}
                                      >
                                        <div className="connector-lane nested" aria-hidden="true"></div>
                                        <div className="commit-node">
                                            <div className="commit-hash-message">
                                              <span className="commit-hash">
                                                {nested.link ? (
                                                  <a href={nested.link} target="_blank" rel="noopener noreferrer">
                                                    {nested.sha?.slice(0, 7) ?? 'unknown'}
                                                  </a>
                                                ) : (
                                                  nested.sha?.slice(0, 7) ?? 'unknown'
                                                )}
                                              </span>
                                              <span className="commit-message-text">{nested.message || 'No message'}</span>
                                            </div>
                                            <div className="text-muted small">
                                              {nested.author || 'Unknown'} · {nested.date ? new Date(nested.date).toLocaleString() : 'Unknown'}
                                            </div>
                                        </div>
                                        </li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  </div>
                  ))}
                </div>
              )}
          </div>
        </div>
      ) : (
        <TicketsList tickets={ticketsByView[activeView] || []} />
      )}
    </div>
  );
}

export default App;
