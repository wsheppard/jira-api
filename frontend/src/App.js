import { useEffect, useMemo, useState } from 'react';
import './App.css';

const API_BASE_URL = 'https://jira.api.jjrsoftware.co.uk';

const POSITION_LABELS = {
  pull_request: 'Pull request open',
  master: 'In master',
  merged_not_master: 'Merged outside master',
  built: 'Feature build available',
  ticket_only: 'Ticket only',
};

const POSITION_CLASSES = {
  pull_request: 'position-pr',
  master: 'position-master',
  merged_not_master: 'position-warning',
  built: 'position-built',
  ticket_only: 'position-ticket',
};

function TicketRow({ ticket }) {
  return (
    <article className="ticket-row">
      <div className="ticket-identity">
        <a href={ticket.url} target="_blank" rel="noreferrer" className="ticket-key">
          {ticket.key}
        </a>
        <div className="ticket-summary">{ticket.summary}</div>
      </div>

      <div className="ticket-position">
        <span className={`position-badge ${POSITION_CLASSES[ticket.position]}`}>
          {POSITION_LABELS[ticket.position]}
        </span>
        <span className="secondary-label">{ticket.status}</span>
      </div>

      <div className="ticket-links">
        {ticket.pull_requests.map((pullRequest) => (
          <a
            className="pull-request-link"
            key={pullRequest.url}
            href={pullRequest.url}
            target="_blank"
            rel="noreferrer"
          >
            <span>{pullRequest.title}</span>
            <small>{pullRequest.merged ? 'Merged' : 'Open'}</small>
          </a>
        ))}
        {ticket.delivery_error && <span className="warning-text">{ticket.delivery_error}</span>}
      </div>

      <div className="ticket-builds">
        {ticket.feature_builds.map((build) => (
          <span className="build-label" key={build}>{build}</span>
        ))}
        {!ticket.feature_builds.length && <span className="empty-value">No builds</span>}
      </div>

      <div className="ticket-deployments">
        {ticket.deployments.map((deployment) => (
          <span className="deployment-label" key={deployment}>{deployment}</span>
        ))}
        {!ticket.deployments.length && <span className="empty-value">Not deployed</span>}
      </div>
    </article>
  );
}

function App() {
  const [stack, setStack] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    fetch(`${API_BASE_URL}/delivery-stack`)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error('The delivery map is unavailable.');
        }
        return response.json();
      })
      .then((payload) => {
        if (active) setStack(payload);
      })
      .catch((requestError) => {
        if (active) setError(requestError.message);
      });
    return () => {
      active = false;
    };
  }, []);

  const deploymentSummary = useMemo(() => {
    if (!stack) return [];
    return stack.deployments.filter((deployment) => deployment.image_tag || deployment.error);
  }, [stack]);

  if (error) {
    return <main className="page-shell"><div className="error-panel">{error}</div></main>;
  }

  if (!stack) {
    return (
      <main className="page-shell loading-shell">
        <div className="spinner-border text-primary" role="status" aria-label="Loading delivery map" />
        <span>Loading delivery map…</span>
      </main>
    );
  }

  return (
    <main className="page-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">Palliativa</p>
          <h1>Delivery map</h1>
          <p className="subtitle">Release, master, feature work, builds and deployments in one place.</p>
        </div>
        <time dateTime={stack.generated_at}>
          Updated {new Date(stack.generated_at).toLocaleString()}
        </time>
      </header>

      <section className="delivery-layers" aria-label="Release and master">
        <a className="layer-card release-layer" href={stack.release.url} target="_blank" rel="noreferrer">
          <span className="layer-label">Production release</span>
          <strong>{stack.release.version}</strong>
        </a>
        <div className="layer-arrow" aria-hidden="true">→</div>
        <a className="layer-card master-layer" href={stack.master.url} target="_blank" rel="noreferrer">
          <span className="layer-label">Master</span>
          <strong>{stack.master.commits_since_release} commits ahead</strong>
          <small>{stack.master.tickets_in_master} current tickets confirmed in master</small>
        </a>
        <div className="deployment-strip">
          {deploymentSummary.map((deployment) => (
            <a
              key={deployment.env}
              className={`deployment-card ${deployment.healthy ? '' : 'deployment-unhealthy'}`}
              href={deployment.app_url || undefined}
              target={deployment.app_url ? '_blank' : undefined}
              rel={deployment.app_url ? 'noreferrer' : undefined}
            >
              <strong>{deployment.label}</strong>
              <span>{deployment.error || deployment.image_tag || deployment.deploy_kind}</span>
            </a>
          ))}
        </div>
      </section>

      <section className="tickets-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Current work</p>
            <h2>Tickets</h2>
          </div>
          <span>{stack.tickets.length} tickets</span>
        </div>

        <div className="ticket-column-headings" aria-hidden="true">
          <span>Ticket</span>
          <span>Position</span>
          <span>Pull request</span>
          <span>Feature builds</span>
          <span>Running on</span>
        </div>

        <div className="ticket-list">
          {stack.tickets.map((ticket) => <TicketRow key={ticket.key} ticket={ticket} />)}
        </div>
      </section>
    </main>
  );
}

export default App;
