import React, { useState, useEffect, useCallback, useRef } from 'react';
import TicketsList from './TicketsList';
import './App.css';

const API_BASE_URL = 'https://jira.api.jjrsoftware.co.uk';
const STAGING_VIEW_ID = 'codexIntegrationCommits';
const OPEN_CODEX_INTEGRATION_PRS_URL = 'https://github.com/palliativa/monorepo/pulls?q=is%3Apr+is%3Aopen+base%3Acodex%2Fintegration';

const VIEW_CONFIG = {
  open: { label: 'Open Tickets by Due Date', endpoint: 'open-issues-by-due', type: 'tickets' },
  inProgress: { label: 'In Progress Tickets', endpoint: 'in-progress', type: 'tickets' },
  backlog: { label: 'Backlog', endpoint: 'backlog', type: 'tickets' },
  managerMeeting: { label: 'Manager Meeting', endpoint: 'manager-meeting', type: 'tickets' },
  recentActivity: { label: 'Updated Last 72h (excl. last 30m)', endpoint: 'recently-updated', type: 'tickets' },
  recentlyClosed: { label: 'Recently Closed (Last 72h)', endpoint: 'recently-closed', type: 'tickets' },
  codexEnrich: { label: 'Codex Enrich / Enriched', endpoint: 'codex-enrich', type: 'tickets' },
  codexMoreInfo: { label: 'Codex More Info', endpoint: 'codex-more-info', type: 'tickets' },
  codexImplemented: { label: 'Codex Implemented', endpoint: 'codex-implemented', type: 'tickets' },
  codexIntegrationCommits: {
    label: 'Staging View',
    endpoint: 'github-branch-commits?owner=palliativa&repo=monorepo&base=latest-tag&head=codex/integration',
    type: 'githubCommits',
  },
  codexIntegrationPrQueue: {
    label: 'PR Queue -> codex/integration',
    endpoint: 'github-pr-queue?owner=palliativa&repo=monorepo&base=codex/integration',
    type: 'githubPrQueue',
  },
  ticketQuestion: { label: 'Ask Tickets', endpoint: 'ticket-question', type: 'ticketQuestion' },
};

const VIEW_ORDER = [
  'open',
  'inProgress',
  'backlog',
  'managerMeeting',
  'recentActivity',
  'recentlyClosed',
  'codexEnrich',
  'codexMoreInfo',
  'codexImplemented',
  'codexIntegrationCommits',
  'codexIntegrationPrQueue',
  'ticketQuestion',
];
const DEFAULT_VIEW = STAGING_VIEW_ID;

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
    recentlyClosed: [],
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
  const [stagingActiveTab, setStagingActiveTab] = useState('jiraCards');
  const [githubRefreshInProgress, setGithubRefreshInProgress] = useState(false);
  const [githubPrQueue, setGithubPrQueue] = useState([]);
  const [prQueueSearch, setPrQueueSearch] = useState('');
  const [mergeInProgressByTicket, setMergeInProgressByTicket] = useState({});
  const [mergeMessageByTicket, setMergeMessageByTicket] = useState({});
  const [backfillInProgress, setBackfillInProgress] = useState(false);
  const [backfillMessage, setBackfillMessage] = useState('');
  const [ticketQuestionInput, setTicketQuestionInput] = useState('');
  const [ticketQuestionResult, setTicketQuestionResult] = useState(null);
  const [ticketQuestionRunning, setTicketQuestionRunning] = useState(false);
  const [ticketCreateRunning, setTicketCreateRunning] = useState(false);
  const [ticketAssistantLastText, setTicketAssistantLastText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
const [nextPollIn, setNextPollIn] = useState(30);
  const pendingRequests = useRef(0);
  const groupOrderRef = useRef(new Map());
  const hasSyncedInitialPath = useRef(false);
  const activeConfig = VIEW_CONFIG[activeView];
  const pollIntervalMs = activeConfig?.type === 'githubCommits'
    ? 300000
    : activeConfig?.type === 'githubPrQueue'
    ? 300000
    : activeConfig?.type === 'ticketQuestion'
      ? 0
      : 30000;
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

  const postJson = useCallback(async (endpoint, body = null) => {
    const options = { method: 'POST' };
    if (body != null) {
      options.headers = { 'Content-Type': 'application/json' };
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(`${API_BASE_URL}/${endpoint}`, options);
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

  const fetchViewData = useCallback(async (view, options = {}) => {
    const config = VIEW_CONFIG[view];
    if (!config) {
      return;
    }
    const forceGithubRefresh = options?.forceGithubRefresh === true;
    setNextPollIn(pollIntervalSeconds);
    if (config.type === 'ticketQuestion') {
      return;
    }

    markRequestStart();
    setErrorMessage('');
    try {
      if (config.type === 'githubPrQueue') {
        const data = await fetchJson(config.endpoint);
        setGithubPrQueue(Array.isArray(data?.prs) ? data.prs : []);
      } else if (config.type === 'githubCommits') {
        const stagingData = await fetchJson(`staging-tickets?project=AP&version=${encodeURIComponent(stagingVersion || 'next')}`);
        setStagingTickets(Array.isArray(stagingData?.tickets) ? stagingData.tickets : []);
        setStagingReleaseParent(stagingData?.release_parent ?? null);
        setStagingAvailableVersions(Array.isArray(stagingData?.available_versions) ? stagingData.available_versions : []);
        setStagingResolvedVersion(stagingData?.resolved_version || '');
        setStagingNextVersion(stagingData?.next_version || '');
        const compareVersion = stagingData?.resolved_version || stagingVersion || 'next';
        const endpointWithVersion = `${config.endpoint}&version=${encodeURIComponent(compareVersion)}`
          + `${forceGithubRefresh ? '&force_refresh=1&refresh_nonce=' + Date.now() : ''}`;
        const data = await fetchJson(endpointWithVersion);
        setGithubCommits(Array.isArray(data?.commits) ? data.commits : []);
        setGithubCompare(data ?? null);
        const prQueueData = await fetchJson('github-pr-queue?owner=palliativa&repo=monorepo&base=codex/integration');
        setGithubPrQueue(Array.isArray(prQueueData?.prs) ? prQueueData.prs : []);
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
    if (activeView !== STAGING_VIEW_ID) {
      setStagingActiveTab('jiraCards');
    }
  }, [activeView]);

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
    if (pollIntervalMs <= 0) {
      return undefined;
    }
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
  const commitHasReadyForReleaseJira = (commit) =>
    Array.isArray(commit?.jira)
    && commit.jira.length > 0
    && commit.jira.some((jiraItem) => isReadyForRelease(jiraItem?.status));

  const codexIntegrationTagPattern = /^codex-int(?:egration|ergration)-(\d+)$/i;
  const isCodexIntegrationTag = (tag) => typeof tag === 'string' && codexIntegrationTagPattern.test(tag.trim());
  const sortCodexIntegrationTags = (tags) => (
    [...new Set((Array.isArray(tags) ? tags : []).filter(isCodexIntegrationTag))]
      .sort((a, b) => {
        const aMatch = a.match(codexIntegrationTagPattern);
        const bMatch = b.match(codexIntegrationTagPattern);
        const aNum = aMatch ? Number(aMatch[1]) : Number.NaN;
        const bNum = bMatch ? Number(bMatch[1]) : Number.NaN;
        if (!Number.isNaN(aNum) && !Number.isNaN(bNum) && aNum !== bNum) {
          return aNum - bNum;
        }
        return a.localeCompare(b);
      })
  );

  const buildRangeTags = () => {
    const allTagRows = new Map();
    githubCommits.forEach((commit) => {
      const tags = Array.isArray(commit?.tags) ? commit.tags : [];
      tags.forEach((tag) => {
        if (!tag || allTagRows.has(tag)) {
          return;
        }
        allTagRows.set(tag, {
          tag,
          sha: commit?.sha || '',
          date: commit?.date || '',
        });
      });
    });
    const all = Array.from(allTagRows.values()).sort((a, b) => (a.date || '').localeCompare(b.date || ''));
    return {
      all,
      codex: sortCodexIntegrationTags(all.map((entry) => entry.tag)),
    };
  };

  const buildCommitTagTimeline = () => {
    const nestedCommitShas = new Set();
    githubCommits.forEach((commit) => {
      if (Array.isArray(commit?.nested_commits)) {
        commit.nested_commits.forEach((nested) => {
          if (nested?.sha) {
            nestedCommitShas.add(nested.sha);
          }
        });
      }
    });

    const seenShas = new Set();
    const timelineAsc = [...githubCommits].filter((commit) => {
      const sha = commit?.sha || '';
      if (!sha || nestedCommitShas.has(sha) || seenShas.has(sha)) {
        return false;
      }
      seenShas.add(sha);
      return true;
    });

    const tagAnchors = [];
    timelineAsc.forEach((commit, index) => {
      const tags = Array.isArray(commit?.tags) ? commit.tags.filter(Boolean) : [];
      tags.forEach((tag) => {
        tagAnchors.push({
          tag,
          index,
          sha: commit?.sha || '',
          date: commit?.date || '',
        });
      });
    });

    const includedByIndex = timelineAsc.map(() => new Set());
    tagAnchors.forEach((anchor) => {
      for (let i = 0; i <= anchor.index; i += 1) {
        includedByIndex[i].add(anchor.tag);
      }
    });

    const rowsAsc = timelineAsc.map((commit, index) => {
      const tagsOnCommit = Array.isArray(commit?.tags) ? [...new Set(commit.tags.filter(Boolean))].sort() : [];
      const includedInRangeTags = Array.from(includedByIndex[index]).sort();
      return {
        ...commit,
        tagsOnCommit,
        includedInRangeTags,
        hasTaggedBuild: includedInRangeTags.length > 0,
      };
    });

    const rows = [...rowsAsc].reverse().map((row, index) => ({
      ...row,
      order: index + 1,
    }));

    return {
      rows,
      tagAnchors: [...new Map(tagAnchors.map((anchor) => [anchor.tag, anchor])).values()],
      untaggedRows: rows.filter((row) => !row.hasTaggedBuild),
    };
  };

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
        fixVersions,
        inRelease,
        descriptionText: ticket.descriptionText || '',
        latestComment: (ticket.latestComment && typeof ticket.latestComment === 'object') ? ticket.latestComment : null,
        audienceSummary: ticket.audienceSummary || '',
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

    const ticketCodexTagMap = new Map();
    githubCommits.forEach((commit) => {
      const tags = sortCodexIntegrationTags(commit?.tags || []);
      if (tags.length === 0) {
        return;
      }
      const jiraItems = Array.isArray(commit?.jira) ? commit.jira : [];
      jiraItems.forEach((jiraItem) => {
        if (!jiraItem?.key) {
          return;
        }
        if (!ticketCodexTagMap.has(jiraItem.key)) {
          ticketCodexTagMap.set(jiraItem.key, new Set());
        }
        tags.forEach((tag) => ticketCodexTagMap.get(jiraItem.key).add(tag));
      });
    });

    const allKeys = new Set([...releaseMap.keys(), ...branchMap.keys()]);
    return Array.from(allKeys).map((key) => {
      const releaseData = releaseMap.get(key);
      const branchData = branchMap.get(key);
      const openPrsForTicket = githubPrQueue.filter((pr) => (
        Array.isArray(pr?.tickets) && pr.tickets.some((ticket) => (ticket?.key || '').toUpperCase() === key)
      ));
      const conflictPrs = openPrsForTicket.filter((pr) => pr?.has_merge_conflicts);
      const mergeReadyPrs = openPrsForTicket.filter((pr) => !pr?.draft && !pr?.has_merge_conflicts);
      const labels = Array.from(new Set([...(releaseData?.labels || []), ...(branchData?.labels || [])]));
      const branchFixVersions = branchData?.fixVersions || [];
      const ticketFixVersions = Array.from(new Set([...(releaseData?.fixVersions || []), ...branchFixVersions]));
      const inBranch = Boolean(branchData);
      const inRelease = Boolean(releaseData?.inRelease) || (resolved ? branchFixVersions.includes(resolved) : false);
      const isMerged = inBranch;
      const canMergePr = !inBranch && mergeReadyPrs.length === 1;
      const hasFixVersion = ticketFixVersions.length > 0;
      const isOutsideSelectedRelease = Boolean(resolved) && hasFixVersion && !ticketFixVersions.includes(resolved);
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
        ticketFixVersions,
        hasFixVersion,
        isOutsideSelectedRelease,
        mergedWhere,
        mergedWhereDetail,
        canMergePr,
        openPrCount: openPrsForTicket.length,
        mergeReadyPrCount: mergeReadyPrs.length,
        conflictPrCount: conflictPrs.length,
        mergedCodexIntegrationTags: sortCodexIntegrationTags(Array.from(ticketCodexTagMap.get(key) || [])),
        isReleaseParent: labels.includes('release-ticket') || labels.includes('release-train'),
        descriptionText: releaseData?.descriptionText || '',
        latestComment: releaseData?.latestComment || null,
        audienceSummary: releaseData?.audienceSummary || '',
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

  const handleForceGithubRefresh = useCallback(async () => {
    if (activeView !== STAGING_VIEW_ID) {
      return;
    }
    setGithubRefreshInProgress(true);
    try {
      await fetchViewData(STAGING_VIEW_ID, { forceGithubRefresh: true });
    } finally {
      setGithubRefreshInProgress(false);
    }
  }, [activeView, fetchViewData]);

  const handleMergeTicketPr = useCallback(async (ticketKey) => {
    if (!ticketKey) {
      return;
    }
    const confirmed = typeof window === 'undefined'
      ? true
      : window.confirm(`Merge open PR for ${ticketKey} into codex/integration?`);
    if (!confirmed) {
      return;
    }
    setMergeInProgressByTicket((prev) => ({ ...prev, [ticketKey]: true }));
    setMergeMessageByTicket((prev) => ({ ...prev, [ticketKey]: '' }));
    try {
      const payload = await postJson(
        `github-merge-ticket-pr?ticket=${encodeURIComponent(ticketKey)}&owner=palliativa&repo=monorepo&base=codex/integration`,
      );
      const prNumber = payload?.pr?.number;
      const message = payload?.message || 'Merged successfully.';
      setMergeMessageByTicket((prev) => ({
        ...prev,
        [ticketKey]: prNumber ? `Merged PR #${prNumber}. ${message}` : message,
      }));
      await fetchViewData(STAGING_VIEW_ID, { forceGithubRefresh: true });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'PR merge failed.';
      setMergeMessageByTicket((prev) => ({ ...prev, [ticketKey]: `Merge failed: ${message}` }));
    } finally {
      setMergeInProgressByTicket((prev) => ({ ...prev, [ticketKey]: false }));
    }
  }, [fetchViewData, postJson]);

  const handleAskTicketQuestion = useCallback(async () => {
    const question = ticketQuestionInput.trim();
    if (!question) {
      setErrorMessage('Enter a question first.');
      return;
    }
    setTicketAssistantLastText(question);
    setTicketQuestionRunning(true);
    setErrorMessage('');
    try {
      const data = await postJson('ticket-assistant', { text: question, limit: 40, dry_run: true });
      setTicketQuestionResult(data ?? null);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Ticket question failed.';
      setErrorMessage(message);
    } finally {
      setTicketQuestionRunning(false);
    }
  }, [postJson, ticketQuestionInput]);

  const handleConfirmCreateTickets = useCallback(async () => {
    const text = ticketAssistantLastText.trim();
    if (!text) {
      setErrorMessage('No pending create request found.');
      return;
    }
    const approvalToken = String(ticketQuestionResult?.data?.approval_token || '').trim();
    if (!approvalToken) {
      setErrorMessage('Approval token missing. Run Ask again to generate a fresh dry run before creating.');
      return;
    }
    setTicketCreateRunning(true);
    setErrorMessage('');
    try {
      const data = await postJson('ticket-assistant', { text, limit: 40, dry_run: false, approval_token: approvalToken });
      setTicketQuestionResult(data ?? null);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Ticket creation failed.';
      setErrorMessage(message);
    } finally {
      setTicketCreateRunning(false);
    }
  }, [postJson, ticketAssistantLastText, ticketQuestionResult]);

  const handleCancelCreateTickets = useCallback(() => {
    setTicketQuestionResult((prev) => {
      if (!prev) {
        return prev;
      }
      return {
        ...prev,
        message: 'Creation cancelled.',
      };
    });
  }, []);

  const commitGroups = buildCommitGroups();
  const filteredPrQueue = githubPrQueue.filter((pr) => {
    const query = prQueueSearch.trim().toLowerCase();
    if (!query) {
      return true;
    }
    const ticketText = Array.isArray(pr?.tickets)
      ? pr.tickets.map((ticket) => `${ticket?.key || ''} ${ticket?.summary || ''}`).join(' ')
      : '';
    const haystack = [
      String(pr?.number || ''),
      pr?.title || '',
      pr?.head_ref || '',
      pr?.author || '',
      ticketText,
    ].join(' ').toLowerCase();
    return haystack.includes(query);
  });
  const rangeTags = buildRangeTags();
  const commitTagTimeline = buildCommitTagTimeline();
  const commitGroupByKey = new Map(
    commitGroups.filter((group) => group.key !== 'NO-JIRA').map((group) => [group.key, group]),
  );
  const releaseReconciliation = buildReleaseReconciliation();
  const mergedReleaseItems = releaseReconciliation.filter((item) => item.isMerged);
  const nonMergedReleaseItems = releaseReconciliation.filter((item) => !item.isMerged);

  const renderStagingJiraCard = (item) => {
    const commitsForItem = commitGroupByKey.get(item.key)?.commits || [];
    const hasCodeEvidence = item.isMerged || commitsForItem.length > 0 || item.openPrCount > 0;
    const noCode = !hasCodeEvidence;
    const hasMultipleReadyPrs = !item.isMerged && item.mergeReadyPrCount > 1;
    const hasDraftOnlyPrs = !item.isMerged
      && item.openPrCount > 0
      && item.mergeReadyPrCount === 0
      && item.conflictPrCount === 0;

    return (
      <div
        className={`card h-100 staging-card ${isReadyForRelease(item.status) ? 'staging-status-ready' : 'staging-status-not-ready'}`}
      >
        <div className="card-header staging-status-header">
          <div className="d-flex align-items-start justify-content-between gap-2">
            <div className="d-flex align-items-center gap-2">
              {item.link ? (
                <a href={item.link} target="_blank" rel="noopener noreferrer" className="fw-semibold">
                  {item.key}
                </a>
              ) : (
                <span className="fw-semibold">{item.key}</span>
              )}
              {!item.inBranch && item.canMergePr && (
                <button
                  type="button"
                  className="btn btn-sm btn-outline-success"
                  disabled={mergeInProgressByTicket[item.key] === true}
                  onClick={() => handleMergeTicketPr(item.key)}
                >
                  {mergeInProgressByTicket[item.key] ? 'Merging PR...' : 'Merge PR'}
                </button>
              )}
            </div>
            <div className="d-flex flex-wrap justify-content-end gap-2 text-end">
              {item.status && (
                <span className={`badge ${isReadyForRelease(item.status) ? 'text-bg-success' : 'text-bg-secondary'}`}>
                  {item.status}
                </span>
              )}
              {item.isMerged && <span className="badge text-bg-success">MERGED</span>}
              {!item.isMerged && item.canMergePr && <span className="badge text-bg-warning">PR-READY</span>}
              {!item.isMerged && item.conflictPrCount > 0 && <span className="badge text-bg-danger">PR-CONFLICT</span>}
              {hasDraftOnlyPrs && <span className="badge text-bg-secondary">PR-DRAFT</span>}
              {hasMultipleReadyPrs && (
                <span className="badge text-bg-warning">{`PR-AMBIGUOUS (${item.mergeReadyPrCount})`}</span>
              )}
              {noCode && <span className="badge text-bg-dark">NO CODE</span>}
              {Array.isArray(item.ticketFixVersions) && item.ticketFixVersions.length > 0 && (
                <span className="badge text-bg-info">
                  {`Fix Version: ${item.ticketFixVersions.join(', ')}`}
                </span>
              )}
              {item.isOutsideSelectedRelease && (
                <span className="badge text-bg-warning">
                  {`Outside Selected Release (${stagingResolvedVersion})`}
                </span>
              )}
              {item.isMerged && item.mergedWhere && (
                <span className="badge text-bg-light border">{item.mergedWhere}</span>
              )}
              {item.isMerged && item.mergedCodexIntegrationTags.length > 0 && (
                <span className="badge text-bg-light border">
                  {`Reachable Tag: ${item.mergedCodexIntegrationTags[item.mergedCodexIntegrationTags.length - 1]}`}
                </span>
              )}
              {item.inBranch && !item.hasFixVersion && (
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
          {item.audienceSummary && (
            <div className="small mt-2 staging-audience-summary">
              {item.audienceSummary}
            </div>
          )}
          {mergeMessageByTicket[item.key] && (
            <div className={`small mt-2 ${mergeMessageByTicket[item.key].startsWith('Merge failed:') ? 'text-danger' : 'text-success'}`}>
              {mergeMessageByTicket[item.key]}
            </div>
          )}
          {item.isMerged && item.mergedWhereDetail && (
            <div className="text-muted small mt-1">
              Range: {item.mergedWhereDetail}
            </div>
          )}
          {item.isMerged && (
            <div className="small mt-2">
              <span className="text-muted me-1">Codex-integration tag history:</span>
              {item.mergedCodexIntegrationTags.length > 0 ? (
                item.mergedCodexIntegrationTags.map((tag) => (
                  <span key={`${item.key}-codex-tag-${tag}`} className="badge staging-tag-badge me-1">{tag}</span>
                ))
              ) : (
                <span className="text-muted">No codex-integration tag on commits in this range.</span>
              )}
            </div>
          )}
        </div>
        <div className="card-body">
          {commitsForItem.length === 0 ? (
            <div className="text-muted small">
              {noCode
                ? 'No commits and no open PR for this ticket.'
                : !item.isMerged && item.inRelease
                  ? 'In Jira Fix Version, but no commits found in this selected compare range.'
                  : item.isMerged
                    ? 'Merged, but no commits found in this selected compare range.'
                    : 'No commits found in this selected compare range.'}
            </div>
          ) : (
            <ul className="list-group list-group-flush">
              {commitsForItem.map((commit) => {
                const hasNested = Array.isArray(commit.nested_commits) && commit.nested_commits.length > 0;
                return (
                  <li
                    key={`${item.key}-${commit.sha}`}
                    className={`list-group-item px-0 ${commitHasReadyForReleaseJira(commit) ? 'staging-commit-ready-item' : ''}`}
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
          )}
        </div>
      </div>
    );
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

      {activeConfig?.type === 'githubCommits' ? (
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
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-primary"
                    onClick={handleForceGithubRefresh}
                    disabled={githubRefreshInProgress}
                  >
                    {githubRefreshInProgress ? 'Refreshing GitHub...' : 'Force GitHub Refresh'}
                  </button>
                  <a
                    href={OPEN_CODEX_INTEGRATION_PRS_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn btn-sm btn-outline-dark"
                  >
                    Open PR Queue -&gt; codex/integration
                  </a>
                </div>
                {backfillMessage && <div className="small text-muted mb-2">{backfillMessage}</div>}
                {!stagingReleaseParent && <div className="text-muted small">No release ticket found for this version.</div>}
                <div className="small text-muted">
                  Tags in selected hash range: {rangeTags.all.length}
                  {rangeTags.codex.length > 0 ? ` (codex-integration: ${rangeTags.codex.length})` : ''}
                </div>
                {rangeTags.codex.length > 0 && (
                  <div className="mt-2 d-flex flex-wrap gap-1">
                    {rangeTags.codex.map((tag) => (
                      <span key={`range-codex-tag-${tag}`} className="badge staging-tag-badge">{tag}</span>
                    ))}
                  </div>
                )}
              </div>
              <div className="mb-3">
                <ul className="nav nav-tabs">
                  <li className="nav-item">
                    <button
                      type="button"
                      className={`nav-link ${stagingActiveTab === 'jiraCards' ? 'active' : ''}`}
                      onClick={() => setStagingActiveTab('jiraCards')}
                      aria-current={stagingActiveTab === 'jiraCards' ? 'page' : undefined}
                    >
                      Jira Cards
                    </button>
                  </li>
                  <li className="nav-item">
                    <button
                      type="button"
                      className={`nav-link ${stagingActiveTab === 'commitTimeline' ? 'active' : ''}`}
                      onClick={() => setStagingActiveTab('commitTimeline')}
                      aria-current={stagingActiveTab === 'commitTimeline' ? 'page' : undefined}
                    >
                      Commit Tag Timeline
                    </button>
                  </li>
                </ul>

                {stagingActiveTab === 'commitTimeline' && (
                  <div className="card border mt-3">
                    <div className="card-header d-flex flex-wrap align-items-center gap-2">
                      <span className="fw-semibold">Commit Tag Timeline</span>
                      <span className="badge text-bg-light border">{commitTagTimeline.rows.length} commits</span>
                      <span className="badge text-bg-light border">{commitTagTimeline.tagAnchors.length} tag points</span>
                      <span className={`badge ${commitTagTimeline.untaggedRows.length > 0 ? 'text-bg-warning' : 'text-bg-success'}`}>
                        {commitTagTimeline.untaggedRows.length} with no tag reachable in selected range
                      </span>
                    </div>
                    <div className="card-body p-0">
                      {commitTagTimeline.rows.length === 0 ? (
                        <div className="p-3 text-muted small">No commits in selected range.</div>
                      ) : (
                        <div className="table-responsive">
                          <table className="table table-sm mb-0 align-middle">
                            <thead>
                              <tr>
                                <th style={{ width: '5rem' }}>#</th>
                                <th>Commit</th>
                                <th style={{ minWidth: '12rem' }}>Jira</th>
                              </tr>
                            </thead>
                            <tbody>
                              {commitTagTimeline.rows.map((row) => (
                                <tr
                                  key={`timeline-${row.sha}`}
                                  className={commitHasReadyForReleaseJira(row) ? 'staging-commit-ready' : ''}
                                >
                                  <td className="text-muted">{row.order}</td>
                                  <td>
                                    <div className="commit-hash-message">
                                      <span className="commit-hash">
                                        {row.link ? (
                                          <a href={row.link} target="_blank" rel="noopener noreferrer">
                                            {row.sha?.slice(0, 7) ?? 'unknown'}
                                          </a>
                                        ) : (
                                          row.sha?.slice(0, 7) ?? 'unknown'
                                        )}
                                      </span>
                                      <span className="commit-message-text">{row.message || 'No message'}</span>
                                    </div>
                                    <div className="text-muted small">
                                      {row.author || 'Unknown'} · {row.date ? new Date(row.date).toLocaleString() : 'Unknown'}
                                    </div>
                                    {row.is_merge_commit && renderPrLinks(row.prs) && (
                                      <div className="small mt-1">PRs: {renderPrLinks(row.prs)}</div>
                                    )}
                                    {row.tagsOnCommit.length > 0 && (
                                      <div className="mt-1">
                                        {row.tagsOnCommit.map((tag) => (
                                          <span key={`${row.sha}-on-${tag}`} className="badge text-bg-secondary me-1">{tag}</span>
                                        ))}
                                      </div>
                                    )}
                                    <div className="small mt-1">
                                      <span className="text-muted me-1">Reachable in range:</span>
                                      {row.hasTaggedBuild ? (
                                        <>
                                          {row.includedInRangeTags.slice(0, 2).map((tag) => (
                                            <span key={`${row.sha}-reachable-${tag}`} className="badge staging-tag-badge me-1">{tag}</span>
                                          ))}
                                          {row.includedInRangeTags.length > 2 && (
                                            <span className="text-muted">+{row.includedInRangeTags.length - 2} more</span>
                                          )}
                                        </>
                                      ) : (
                                        <span className="badge text-bg-warning">None</span>
                                      )}
                                    </div>
                                  </td>
                                  <td>
                                    {Array.isArray(row.jira) && row.jira.length > 0 ? (
                                      row.jira.map((jiraItem) => (
                                        <div key={`${row.sha}-jira-${jiraItem.key}`} className="mb-1">
                                          {jiraItem.sourceKey && jiraItem.sourceKey !== jiraItem.key && (
                                            <div className="text-muted small">
                                              Moved from {jiraItem.sourceKey}
                                            </div>
                                          )}
                                          {jiraItem.link ? (
                                            <a href={jiraItem.link} target="_blank" rel="noopener noreferrer">
                                              {jiraItem.key}
                                            </a>
                                          ) : (
                                            <span>{jiraItem.key}</span>
                                          )}
                                          {jiraItem.status && (
                                            <span className={`badge ms-2 ${isReadyForRelease(jiraItem.status) ? 'text-bg-success' : 'text-bg-secondary'}`}>
                                              {jiraItem.status}
                                            </span>
                                          )}
                                          {jiraItem.summary && (
                                            <div className="text-muted small">{jiraItem.summary}</div>
                                          )}
                                        </div>
                                      ))
                                    ) : (
                                      <span className="text-muted small">none</span>
                                    )}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {stagingActiveTab === 'jiraCards' && (
                  <div className="row g-3 mt-1">
                    <div className="col-12">
                      <div className="d-flex align-items-center gap-2 mt-1 mb-1">
                        <span className="fw-semibold">Not Merged</span>
                        <span className="badge text-bg-secondary">{nonMergedReleaseItems.length}</span>
                      </div>
                    </div>
                    {nonMergedReleaseItems.map((item) => (
                      <div key={`not-merged-${item.key}`} className="col-12 col-xl-6">
                        {renderStagingJiraCard(item)}
                      </div>
                    ))}
                    <div className="col-12 mt-2">
                      <div className="d-flex align-items-center gap-2 mb-1">
                        <span className="fw-semibold">Merged</span>
                        <span className="badge text-bg-success">{mergedReleaseItems.length}</span>
                      </div>
                    </div>
                    {mergedReleaseItems.map((item) => (
                      <div key={`merged-${item.key}`} className="col-12 col-xl-6">
                        {renderStagingJiraCard(item)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
          </div>
        </div>
      ) : activeConfig?.type === 'githubPrQueue' ? (
        <div className="card shadow-sm">
          <div className="card-body">
            <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
              <div className="fw-semibold d-flex align-items-center gap-2">
                <span>Open PRs -> codex/integration</span>
                <span className="badge text-bg-primary">{filteredPrQueue.length}</span>
                {prQueueSearch.trim() && (
                  <span className="badge text-bg-light border">of {githubPrQueue.length}</span>
                )}
              </div>
              <div className="d-flex align-items-center gap-2">
                <button
                  type="button"
                  className="btn btn-sm btn-outline-primary"
                  onClick={() => fetchViewData(activeView)}
                >
                  Refresh
                </button>
                <a
                  href={OPEN_CODEX_INTEGRATION_PRS_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-sm btn-outline-dark"
                >
                  Open on GitHub
                </a>
              </div>
            </div>

            <div className="mb-3">
              <label htmlFor="prQueueSearchInput" className="form-label fw-semibold mb-1">Search tickets</label>
              <input
                id="prQueueSearchInput"
                type="search"
                className="form-control"
                value={prQueueSearch}
                onChange={(event) => setPrQueueSearch(event.target.value)}
                placeholder="e.g. AP-123, invoice, payment, release"
              />
            </div>

            {filteredPrQueue.length === 0 ? (
              <p className="text-muted fst-italic mb-0">No open PRs match the current search.</p>
            ) : (
              <div className="table-responsive">
                <table className="table table-sm align-middle mb-0">
                  <thead>
                    <tr>
                      <th>PR</th>
                      <th>Title</th>
                      <th>Merge</th>
                      <th>Ticket(s)</th>
                      <th>Head Branch</th>
                      <th>Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredPrQueue.map((pr) => (
                      <tr key={`queue-pr-${pr.number}`}>
                        <td>
                          {pr.url ? (
                            <a href={pr.url} target="_blank" rel="noopener noreferrer">#{pr.number}</a>
                          ) : (
                            <span>#{pr.number}</span>
                          )}
                          {pr.draft && <span className="badge text-bg-warning ms-2">DRAFT</span>}
                          {pr.has_merge_conflicts && <span className="badge text-bg-danger ms-2">CONFLICT</span>}
                        </td>
                        <td>
                          <div>{pr.title || 'Untitled'}</div>
                          <div className="text-muted small">{pr.author || 'unknown author'}</div>
                        </td>
                        <td>
                          {pr.has_merge_conflicts ? (
                            <span className="text-danger small">Merge conflicts detected</span>
                          ) : (
                            <span className="text-muted small">{pr.mergeable_state || '-'}</span>
                          )}
                        </td>
                        <td>
                          {Array.isArray(pr.tickets) && pr.tickets.length > 0 ? (
                            pr.tickets.map((ticket) => (
                              <div key={`queue-pr-${pr.number}-ticket-${ticket.key || 'unknown'}`} className="mb-1">
                                {ticket?.link ? (
                                  <a href={ticket.link} target="_blank" rel="noopener noreferrer">{ticket.key}</a>
                                ) : (
                                  <span>{ticket?.key || 'Unknown ticket'}</span>
                                )}
                                {ticket?.status && (
                                  <span className="text-muted small"> · {ticket.status}</span>
                                )}
                                {ticket?.summary && (
                                  <div className="text-muted small">{ticket.summary}</div>
                                )}
                              </div>
                            ))
                          ) : (
                            <span className="text-muted small">No ticket found in title/body/branch</span>
                          )}
                        </td>
                        <td>
                          <code>{pr.head_ref || '-'}</code>
                        </td>
                        <td className="text-muted small">
                          {pr.updated_at ? new Date(pr.updated_at).toLocaleString() : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      ) : activeConfig?.type === 'ticketQuestion' ? (
        <div className="card shadow-sm">
          <div className="card-body">
            <div className="d-flex flex-column gap-2 mb-3">
              <label htmlFor="ticketQuestionInput" className="fw-semibold mb-0">Ask about tickets</label>
              <textarea
                id="ticketQuestionInput"
                className="form-control"
                rows={3}
                value={ticketQuestionInput}
                onChange={(event) => setTicketQuestionInput(event.target.value)}
                placeholder="e.g. do we have a ticket for improving invoice export timeouts?"
              />
              <div>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={ticketQuestionRunning}
                  onClick={handleAskTicketQuestion}
                >
                  {ticketQuestionRunning ? 'Searching...' : 'Ask'}
                </button>
              </div>
            </div>

            {ticketQuestionResult && (
              <div className="d-flex flex-column gap-2">
                {ticketQuestionResult.interpretation && (
                  <div className="small text-muted">
                    <strong>Interpretation:</strong> {ticketQuestionResult.interpretation}
                  </div>
                )}
                {ticketQuestionResult.message && (
                  <div className="small text-muted">{ticketQuestionResult.message}</div>
                )}

                {ticketQuestionResult.intent === 'search_tickets' && (
                  <>
                    {ticketQuestionResult?.data?.jql && (
                      <div className="small">
                        <strong>JQL:</strong> <code>{ticketQuestionResult.data.jql}</code>
                      </div>
                    )}
                    <div className="small text-muted">
                      {ticketQuestionResult?.data?.total_matches ?? 0} match(es), showing up to {ticketQuestionResult?.data?.limited_to ?? 0}
                    </div>
                    <TicketsList tickets={Array.isArray(ticketQuestionResult?.data?.tickets) ? ticketQuestionResult.data.tickets : []} />
                  </>
                )}

                {ticketQuestionResult.intent === 'create_linked_software_tickets' && (
                  <div className="card border-light bg-light-subtle">
                    <div className="card-body">
                      <div className="small">
                        <strong>Design ticket:</strong> {ticketQuestionResult?.data?.design_ticket_key || 'Unknown'}
                      </div>
                      <div className="small">
                        <strong>Target project:</strong> {ticketQuestionResult?.data?.target_project || 'Unknown'}
                      </div>
                      <div className="small">
                        <strong>Mode:</strong> {ticketQuestionResult?.data?.dry_run ? 'Dry run' : 'Applied'}
                      </div>
                      <div className="small mt-2">
                        <strong>Implementation tickets:</strong>
                      </div>
                      <ul className="mb-0">
                        {(ticketQuestionResult?.data?.tickets || []).map((item, idx) => (
                          <li key={`ticket-assistant-create-${idx}`}>
                            {item?.summary || item?.key || 'Unnamed ticket'}
                          </li>
                        ))}
                      </ul>
                      {ticketQuestionResult?.data?.dry_run && (
                        <div className="mt-3 d-flex align-items-center gap-2">
                          <span className="small">Create these tickets now?</span>
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            onClick={handleConfirmCreateTickets}
                            disabled={ticketCreateRunning}
                          >
                            {ticketCreateRunning ? 'Creating...' : 'Yes, Create'}
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-outline-secondary"
                            onClick={handleCancelCreateTickets}
                            disabled={ticketCreateRunning}
                          >
                            No
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )}
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
