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

function App() {
  const [activeView, setActiveView] = useState('open');
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
  const pendingRequests = useRef(0);

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
    setActiveView(viewId);
    fetchViewData(viewId);
    hideOffcanvas();
  }, [fetchViewData, hideOffcanvas]);

  useEffect(() => {
    fetchViewData('open');
  }, [fetchViewData]);

  useEffect(() => {
    const interval = setInterval(() => {
      fetchViewData(activeView);
    }, 30000);
    return () => clearInterval(interval);
  }, [activeView, fetchViewData]);

  const activeConfig = VIEW_CONFIG[activeView];

  let viewContent = null;
  if (activeConfig?.type === 'pipeline') {
    if (pipelineCategories.length === 0 && !isLoading) {
      viewContent = <p className="text-muted fst-italic">No pipeline data available.</p>;
    } else {
      viewContent = <PipelineDashboard data={pipelineData} categories={pipelineCategories} />;
    }
  } else {
    viewContent = <TicketsList tickets={ticketsByView[activeView] || []} />;
  }

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

      {viewContent}
    </div>
  );
}

export default App;
