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
  pipeline: { label: 'Pipeline Dashboard', endpoint: 'pipeline-dashboard', type: 'pipeline' },
};

const VIEW_ORDER = ['open', 'inProgress', 'backlog', 'managerMeeting', 'pipeline'];
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
  });
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

  const fetchJson = useCallback(async (endpoint) => {
    const response = await fetch(`${API_BASE_URL}/${endpoint}`);
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${response.statusText}`);
    }
    return response.json();
  }, []);

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
      ) : (
        <TicketsList tickets={ticketsByView[activeView] || []} />
      )}
    </div>
  );
}

export default App;
