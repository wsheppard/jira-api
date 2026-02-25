import React, { useState, useEffect, useCallback, useRef } from 'react';
import TicketsList from './TicketsList';
import PipelineDashboard from './PipelineDashboard';
import './App.css';

const API_BASE_URL = 'https://jira.api.jjrsoftware.co.uk';

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
    label: 'Codex Integration Commits',
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
  const [pipelineData, setPipelineData] = useState({});
  const [pipelineCategories, setPipelineCategories] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
const [nextPollIn, setNextPollIn] = useState(30);
  const pendingRequests = useRef(0);
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
        const data = await fetchJson(config.endpoint);
        setGithubCommits(Array.isArray(data?.commits) ? data.commits : []);
        setGithubCompare(data ?? null);
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
  }, [fetchJson, markRequestEnd, markRequestStart]);

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
      const newPath = pathForView(viewId);
      window.history.pushState({ view: viewId }, '', newPath);
    }
    setActiveView(viewId);
    hideOffcanvas();
  }, [activeView, fetchViewData, hideOffcanvas]);

  useEffect(() => {
    const onPopState = () => {
      const nextView = viewFromLocation(window.location.pathname);
      setActiveView((prev) => (prev === nextView ? prev : nextView));
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  useEffect(() => {
    if (typeof window !== 'undefined' && !hasSyncedInitialPath.current) {
      const desiredPath = pathForView(activeView);
      if (window.location.pathname !== desiredPath) {
        window.history.replaceState({ view: activeView }, '', desiredPath);
      }
      hasSyncedInitialPath.current = true;
    }
    fetchViewData(activeView);
  }, [activeView, fetchViewData]);

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

  const renderJiraLinks = (items) => {
    if (!Array.isArray(items) || items.length === 0) {
      return null;
    }
    return items.map((item, index) => (
      <span key={`${item.key}-${index}`} className="me-2">
        {item.link ? (
          <a href={item.link} target="_blank" rel="noopener noreferrer">
            {item.key}
          </a>
        ) : (
          item.key
        )}
        {item.status ? <span className="text-muted"> ({item.status})</span> : null}
      </span>
    ));
  };

  const buildMasterCommits = () => {
    const commits = Array.isArray(githubCompare?.base_commits) ? [...githubCompare.base_commits] : [];
    if (githubCompare?.base_head) {
      const exists = commits.some((item) => item.sha === githubCompare.base_head.sha);
      if (!exists) {
        commits.push({ ...githubCompare.base_head, label: 'master head' });
      }
    }
    commits.sort((a, b) => (b?.date || '').localeCompare(a?.date || ''));
    return commits;
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
        githubCommits.length === 0 && !isLoading ? (
          <p className="text-muted fst-italic">No commits found for this branch comparison.</p>
        ) : (
          <div className="card shadow-sm">
            <div className="card-body">
              <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
                <div>
                  <div className="fw-semibold">palliativa/monorepo</div>
                  {githubCompare && (
                    <small className="text-muted">
                      {githubCompare.base} → {githubCompare.head} · Ahead {githubCompare.ahead_by ?? 0} · Behind {githubCompare.behind_by ?? 0}
                    </small>
                  )}
                </div>
                {githubCompare && (
                  <span className="badge text-bg-primary">{githubCompare.total_commits ?? githubCommits.length} commits</span>
                )}
              </div>
              <div className="table-responsive">
                <table className="table table-striped align-middle">
                  <thead>
                    <tr>
                      <th scope="col">Commit</th>
                      <th scope="col">Message</th>
                      <th scope="col">Jira</th>
                      <th scope="col">Author</th>
                      <th scope="col">Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {buildMasterCommits().length > 0 && (
                      <tr className="table-light">
                        <td colSpan="5" className="fw-semibold text-muted">Master-only commits</td>
                      </tr>
                    )}
                    {buildMasterCommits().map((commit) => (
                      <tr key={`base-${commit.sha}`} className="table-warning">
                        <td>
                          {commit.link ? (
                            <a href={commit.link} target="_blank" rel="noopener noreferrer">
                              {commit.sha?.slice(0, 7) ?? 'unknown'}
                            </a>
                          ) : (
                            commit.sha?.slice(0, 7) ?? 'unknown'
                          )}
                          {commit.label === 'master head' && (
                            <span className="badge text-bg-dark ms-2">Master head</span>
                          )}
                          {Array.isArray(commit.tags) && commit.tags.length > 0 && (
                            <span className="ms-2">
                              {commit.tags.map((tag) => (
                                <span key={tag} className="badge text-bg-secondary me-1">
                                  {tag}
                                </span>
                              ))}
                            </span>
                          )}
                        </td>
                        <td>{commit.message || 'No message'}</td>
                        <td>{renderJiraLinks(commit.jira)}</td>
                        <td>{commit.author || 'Unknown'}</td>
                        <td>{commit.date ? new Date(commit.date).toLocaleString() : 'Unknown'}</td>
                      </tr>
                    ))}
                    {githubCommits.map((commit) => (
                      <tr key={commit.sha}>
                        <td>
                          {commit.link ? (
                            <a href={commit.link} target="_blank" rel="noopener noreferrer">
                              {commit.sha?.slice(0, 7) ?? 'unknown'}
                            </a>
                          ) : (
                            commit.sha?.slice(0, 7) ?? 'unknown'
                          )}
                          {Array.isArray(commit.tags) && commit.tags.length > 0 && (
                            <span className="ms-2">
                              {commit.tags.map((tag) => (
                                <span key={tag} className="badge text-bg-secondary me-1">
                                  {tag}
                                </span>
                              ))}
                            </span>
                          )}
                        </td>
                        <td>{commit.message || 'No message'}</td>
                        <td>{renderJiraLinks(commit.jira)}</td>
                        <td>{commit.author || 'Unknown'}</td>
                        <td>{commit.date ? new Date(commit.date).toLocaleString() : 'Unknown'}</td>
                      </tr>
                    ))}
                    {githubCompare?.merge_base && (
                      <tr className="table-warning">
                        <td>
                          {githubCompare.merge_base.link ? (
                            <a href={githubCompare.merge_base.link} target="_blank" rel="noopener noreferrer">
                              {githubCompare.merge_base.sha?.slice(0, 7) ?? 'unknown'}
                            </a>
                          ) : (
                            githubCompare.merge_base.sha?.slice(0, 7) ?? 'unknown'
                          )}
                          <span className="badge text-bg-dark ms-2">Common ancestor</span>
                          {Array.isArray(githubCompare.merge_base.tags) && githubCompare.merge_base.tags.length > 0 && (
                            <span className="ms-2">
                              {githubCompare.merge_base.tags.map((tag) => (
                                <span key={tag} className="badge text-bg-secondary me-1">
                                  {tag}
                                </span>
                              ))}
                            </span>
                          )}
                        </td>
                        <td>{githubCompare.merge_base.message || 'No message'}</td>
                        <td>{renderJiraLinks(githubCompare.merge_base.jira)}</td>
                        <td>{githubCompare.merge_base.author || 'Unknown'}</td>
                        <td>{githubCompare.merge_base.date ? new Date(githubCompare.merge_base.date).toLocaleString() : 'Unknown'}</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )
      ) : (
        <TicketsList tickets={ticketsByView[activeView] || []} />
      )}
    </div>
  );
}

export default App;
