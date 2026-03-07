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
    endpoint: 'github-branch-commits?owner=palliativa&repo=monorepo&base=latest-tag&head=codex/integration',
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
  const [backfillInProgress, setBackfillInProgress] = useState(false);
  const [backfillMessage, setBackfillMessage] = useState('');
  const [pipelineData, setPipelineData] = useState({});
  const [pipelineCategories, setPipelineCategories] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
const [nextPollIn, setNextPollIn] = useState(30);
  const pendingRequests = useRef(0);
  const groupOrderRef = useRef(new Map());
  const hasSyncedInitialPath = useRef(false);
  const activeConfig = VIEW_CONFIG[activeView];
  const pollIntervalMs = activeConfig?.type === 'githubCommits' ? 300000 : 30000;
  const pollIntervalSeconds = Math.floor(pollIntervalMs / 1000);

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

  const postJson = useCallback(async (endpoint) => {
    let response;
    try {
      response = await fetch(`${API_BASE_URL}/${endpoint}`, { method: 'POST' });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unexpected error while posting data.';
      throw new Error(`Failed to post ${endpoint}: ${message}`);
    }
    if (!response.ok) {
      let detail = '';
      try {
        const body = await response.json();
        detail = summarizeErrorDetail(body?.detail ?? body);
      } catch (jsonError) {
        detail = '';
      }
      throw new Error(`Request failed: ${response.status} ${response.statusText}${detail ? ` - ${detail}` : ''}`);
    }
    return response.json();
  }, [summarizeErrorDetail]);

  const fetchViewData = useCallback(async (view) => {
    const config = VIEW_CONFIG[view];
    if (!config) {
      return;
    }
    setNextPollIn(pollIntervalSeconds);

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
        const compareVersion = stagingData?.resolved_version || stagingVersion || 'next';
        const endpointWithVersion = `${config.endpoint}&version=${encodeURIComponent(compareVersion)}`;
        const data = await fetchJson(endpointWithVersion);
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
  }, [fetchJson, markRequestEnd, markRequestStart, pollIntervalSeconds, stagingVersion]);

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
    }, pollIntervalMs);
    return () => clearInterval(interval);
  }, [activeView, fetchViewData, pollIntervalMs]);
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
    const nonEmptyGroups = groupList.filter((group) => group.commits.length > 0);
    const orderMap = groupOrderRef.current;
    let nextIndex = orderMap.size;
    nonEmptyGroups.forEach((group) => {
      if (!orderMap.has(group.key)) {
        orderMap.set(group.key, nextIndex);
        nextIndex += 1;
      }
    });
    nonEmptyGroups.sort((a, b) => {
      if (a.key === noJiraKey) return 1;
      if (b.key === noJiraKey) return -1;
      return (orderMap.get(a.key) ?? 0) - (orderMap.get(b.key) ?? 0);
    });
    return nonEmptyGroups;
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

  const isReadyForRelease = (statusName) =>
    typeof statusName === 'string' && statusName.trim().toLowerCase() === 'ready for release';

  const buildReleaseReconciliation = () => {
    const resolved = stagingResolvedVersion;
    const compareFromRef = githubCompare?.from_ref || '';
    const compareToRef = githubCompare?.to_ref || '';
    const releaseMap = new Map();
    const upsertRelease = (ticket) => {
      if (!ticket?.ticket) return;
      const fixVersions = Array.isArray(ticket.fixVersions) ? ticket.fixVersions : [];
      const inRelease = resolved ? fixVersions.includes(resolved) : false;
      releaseMap.set(ticket.ticket, {
        key: ticket.ticket,
        title: ticket.title || '',
        status: ticket.statusName || '',
        link: ticket.link || '',
        labels: Array.isArray(ticket.labels) ? ticket.labels : [],
        inRelease,
      });
    };
    if (stagingReleaseParent) {
      upsertRelease(stagingReleaseParent);
    }
    stagingTickets.forEach(upsertRelease);

    const branchMap = new Map();
    githubCommits.forEach((commit) => {
      const jiraItems = Array.isArray(commit?.jira) ? commit.jira : [];
      jiraItems.forEach((jiraItem) => {
        if (!jiraItem?.key) return;
        if (!branchMap.has(jiraItem.key)) {
          branchMap.set(jiraItem.key, {
            key: jiraItem.key,
            title: jiraItem.summary || '',
            status: jiraItem.status || '',
            link: jiraItem.link || '',
            labels: Array.isArray(jiraItem.labels) ? jiraItem.labels : [],
            fixVersions: Array.isArray(jiraItem.fixVersions) ? jiraItem.fixVersions : [],
          });
        }
      });
    });

    const allKeys = new Set([...releaseMap.keys(), ...branchMap.keys()]);
    return Array.from(allKeys).map((key) => {
      const releaseData = releaseMap.get(key);
      const branchData = branchMap.get(key);
      const labels = Array.from(new Set([...(releaseData?.labels || []), ...(branchData?.labels || [])]));
      const branchFixVersions = branchData?.fixVersions || [];
      const inBranch = Boolean(branchData);
      const inRelease = Boolean(releaseData?.inRelease) || (resolved ? branchFixVersions.includes(resolved) : false);
      const isMerged = inBranch;
      const isReleaseTaggedOnly = inRelease && !inBranch;
      let mergedWhere = '';
      let mergedWhereDetail = '';
      if (inBranch) {
        mergedWhere = compareToRef ? `Seen in ${compareToRef}` : 'Seen in selected compare head';
        mergedWhereDetail = compareFromRef && compareToRef ? `${compareFromRef} -> ${compareToRef}` : '';
      } else if (inRelease) {
        mergedWhere = resolved ? `In Jira Fix Version ${resolved}` : 'In Jira release scope';
      }
      return {
        key,
        title: releaseData?.title || branchData?.title || '',
        status: releaseData?.status || branchData?.status || '',
        link: releaseData?.link || branchData?.link || '',
        labels,
        inBranch,
        inRelease,
        isMerged,
        isReleaseTaggedOnly,
        mergedWhere,
        mergedWhereDetail,
        isReleaseParent: labels.includes('release-ticket') || labels.includes('release-train'),
      };
    }).filter((item) => !item.isReleaseParent).sort((a, b) => {
      if (a.isReleaseParent && !b.isReleaseParent) return -1;
      if (!a.isReleaseParent && b.isReleaseParent) return 1;
      return a.key.localeCompare(b.key);
    });
  };

  const handleBackfillFixVersion = useCallback(async () => {
    if (!stagingResolvedVersion) {
      return;
    }
    setBackfillInProgress(true);
    setBackfillMessage('');
    try {
      const payload = await postJson(`staging-backfill-fix-version?project=AP&version=${encodeURIComponent(stagingResolvedVersion)}`);
      const updatedCount = Array.isArray(payload?.updated) ? payload.updated.length : 0;
      setBackfillMessage(updatedCount > 0 ? `Backfilled Fix Version on ${updatedCount} ticket(s).` : 'No missing Fix Version tickets found.');
      fetchViewData(activeView);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Backfill failed.';
      setBackfillMessage(`Backfill failed: ${message}`);
    } finally {
      setBackfillInProgress(false);
    }
  }, [activeView, fetchViewData, postJson, stagingResolvedVersion]);

  const commitGroups = buildCommitGroups();
  const commitGroupByKey = new Map(
    commitGroups.filter((group) => group.key !== 'NO-JIRA').map((group) => [group.key, group]),
  );
  const noJiraGroup = commitGroups.find((group) => group.key === 'NO-JIRA') || null;

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
                    {githubCompare?.from_ref && githubCompare?.to_ref && (
                      <span className="badge text-bg-light border">
                        Range: {githubCompare.from_ref} -&gt; {githubCompare.to_ref}
                      </span>
                    )}
                    {githubCompare?.from_sha && githubCompare?.to_sha && (
                      <span className="badge text-bg-secondary">
                        {githubCompare.from_sha.slice(0, 7)}..{githubCompare.to_sha.slice(0, 7)}
                      </span>
                    )}
                    {githubCompare?.version_tag_found === false && (
                      <span className="badge text-bg-warning">
                        {`Tag not found for ${githubCompare.requested_release_version || 'selected version'} (unreleased; showing ${githubCompare.from_ref || 'latest tag'} -> ${githubCompare.to_ref || 'target head'})`}
                      </span>
                    )}
                  </div>
                </div>
                <div className="d-flex align-items-center gap-2">
                  <a
                    href={githubCompare?.compare_url || 'https://github.com/palliativa/monorepo/compare'}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn btn-sm btn-outline-primary"
                  >
                    Open Compare on GitHub
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
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-secondary"
                    onClick={handleBackfillFixVersion}
                    disabled={backfillInProgress || !stagingResolvedVersion}
                  >
                    {backfillInProgress ? 'Backfilling...' : 'Backfill Missing Fix Version'}
                  </button>
                </div>
                {backfillMessage && <div className="small text-muted mb-2">{backfillMessage}</div>}
                {!stagingReleaseParent && <div className="text-muted small">No release ticket found for this version.</div>}
              </div>
              <div className="mb-3">
                <div className="row g-3">
                  {noJiraGroup && noJiraGroup.commits.length > 0 && (
                    <div className="col-12 col-xl-6">
                      <div className="card h-100 staging-card staging-status-warning">
                        <div className="card-header staging-status-header d-flex flex-wrap align-items-center gap-2">
                          <span className="fw-semibold">Commits Without Jira</span>
                          <span className="badge text-bg-warning">No Jira</span>
                          <span className="badge text-bg-light border">{noJiraGroup.commits.length} commits</span>
                        </div>
                        <div className="card-body">
                          <ul className="list-group list-group-flush">
                            {noJiraGroup.commits.map((commit) => (
                              <li key={`no-jira-${commit.sha}`} className="list-group-item px-0">
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
                                <div className="text-muted small">
                                  {commit.author || 'Unknown'} · {commit.date ? new Date(commit.date).toLocaleString() : 'Unknown'}
                                </div>
                                {renderPrLinks(commit.prs) && (
                                  <div className="small mt-1">PRs: {renderPrLinks(commit.prs)}</div>
                                )}
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    </div>
                  )}
                  {buildReleaseReconciliation().map((item) => (
                    <div key={item.key} className="col-12 col-xl-6">
                      {(() => {
                        const commitsForItem = commitGroupByKey.get(item.key)?.commits || [];
                        return (
                      <div
                        className={`card h-100 staging-card ${isReadyForRelease(item.status) ? 'staging-status-ready' : 'staging-status-not-ready'}`}
                      >
                      <div className="card-header staging-status-header">
                        <div className="d-flex align-items-start justify-content-between gap-2">
                          {item.link ? (
                            <a href={item.link} target="_blank" rel="noopener noreferrer" className="fw-semibold">
                              {item.key}
                            </a>
                          ) : (
                            <span className="fw-semibold">{item.key}</span>
                          )}
                          <div className="d-flex flex-wrap justify-content-end gap-2 text-end">
                            {item.status && (
                              <span className={`badge ${isReadyForRelease(item.status) ? 'text-bg-success' : 'text-bg-secondary'}`}>
                                {item.status}
                              </span>
                            )}
                            {item.isMerged ? (
                              <span className="badge text-bg-success">MERGED</span>
                            ) : item.isReleaseTaggedOnly ? (
                              <span className="badge text-bg-info">RELEASE TAGGED</span>
                            ) : (
                              <span className="badge text-bg-danger">Not MERGED</span>
                            )}
                            {item.isMerged && item.mergedWhere && (
                              <span className="badge text-bg-light border">{item.mergedWhere}</span>
                            )}
                            {item.inBranch && !item.inRelease && (
                              <span className="badge text-bg-warning">Missing Fix Version</span>
                            )}
                            {Array.isArray(item.labels) && item.labels.map((label) => (
                              <span key={`${item.key}-recon-${label}`} className="badge staging-label-badge">{label}</span>
                            ))}
                          </div>
                        </div>
                        <div className="text-muted mt-2">
                          {item.title}
                        </div>
                        {item.isMerged && item.mergedWhereDetail && (
                          <div className="text-muted small mt-1">
                            Range: {item.mergedWhereDetail}
                          </div>
                        )}
                      </div>
                      <div className="card-body">
                        {commitsForItem.length === 0 ? (
                          <div className="text-muted small">
                            {item.isReleaseTaggedOnly
                              ? 'In Jira Fix Version, but no GitHub commits/PRs found in this selected compare range.'
                              : item.isMerged
                                ? 'Merged, but no commits/PRs in this selected compare range.'
                              : 'No commits / PRs yet.'}
                          </div>
                        ) : (
                          <ul className="list-group list-group-flush">
                            {commitsForItem.map((commit) => {
                              const hasNested = Array.isArray(commit.nested_commits) && commit.nested_commits.length > 0;
                              return (
                                <li key={`${item.key}-${commit.sha}`} className="list-group-item px-0">
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
                        )}
                      </div>
                      </div>
                        );
                      })()}
                    </div>
                  ))}
                </div>
              </div>
          </div>
        </div>
      ) : (
        <TicketsList tickets={ticketsByView[activeView] || []} />
      )}
    </div>
  );
}

export default App;
