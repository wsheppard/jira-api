import React from 'react';

function TicketsList({ tickets }) {
  if (!tickets || tickets.length === 0) {
    return <p className="text-muted fst-italic">No tickets to display.</p>;
  }

  const jiraKeyRegex = /\b([A-Z][A-Z0-9]+-\d+)\b/g;
  const jiraKeyTokenRegex = /^[A-Z][A-Z0-9]+-\d+$/;

  const extractBaseUrl = (ticketLink) => {
    if (!ticketLink) return '';
    try {
      return new URL(ticketLink).origin;
    } catch (error) {
      return '';
    }
  };

  const linkifyJiraKeys = (text, baseUrl) => {
    if (!text) return text;
    const parts = text.split(jiraKeyRegex);
    return parts.map((part, index) => {
      if (baseUrl && jiraKeyTokenRegex.test(part)) {
        return (
          <a
            key={`${part}-${index}`}
            href={`${baseUrl}/browse/${part}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            {part}
          </a>
        );
      }
      return <React.Fragment key={`${part}-${index}`}>{part}</React.Fragment>;
    });
  };

  const timeAgo = (dateString) => {
    if (!dateString) return '';
    const now = new Date();
    const updated = new Date(dateString);
    const diffSec = Math.round((now - updated) / 1000);
    if (diffSec < 60) return `${diffSec} seconds ago`;
    const diffMin = Math.round(diffSec / 60);
    if (diffMin < 60) return `${diffMin} minutes ago`;
    const diffHrs = Math.round(diffMin / 60);
    if (diffHrs < 24) return `${diffHrs} hours ago`;
    return `${Math.round(diffHrs / 24)} days ago`;
  };

  const isOverdue = (dateString) => {
    if (!dateString) return false;
    const due = new Date(dateString + 'T00:00:00');
    return due < new Date();
  };

  const daysOld = (dateString) => {
    return Math.floor((Date.now() - new Date(dateString)) / (1000 * 60 * 60 * 24));
  };

  const dueDeltaLabel = (dueDate) => {
    if (!dueDate) return '';
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const due = new Date(dueDate + 'T00:00:00');
    const diffDays = Math.round((due - today) / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return 'Due today';
    if (diffDays < 0) return `${Math.abs(diffDays)} day${Math.abs(diffDays) === 1 ? '' : 's'} overdue`;
    return `Due in ${diffDays} day${diffDays === 1 ? '' : 's'}`;
  };

  const priorityClass = (priority) => {
    if (!priority) return '';
    const priorityValue = priority.toLowerCase();
    if (priorityValue === 'high') return 'priority-high';
    if (priorityValue === 'medium') return 'priority-medium';
    if (priorityValue === 'low') return 'priority-low';
    return '';
  };

  const getIssueTypeIcon = (issueType) => {
    if (!issueType) return '';
    const issueTypeLower = issueType.toLowerCase();
    if (issueTypeLower === 'bug') {
      return 'fa-solid fa-bug';
    } else if (issueTypeLower === 'story') {
      return 'fa-solid fa-book-open';
    } else if (issueTypeLower === 'task') {
      return 'fa-solid fa-check-square';
    }
    return 'fa-solid fa-question-circle';
  };

  return (
    <div className="tickets-grid mb-5">
      {tickets.map(ticket => {
        const baseUrl = extractBaseUrl(ticket.link);
        return (
          <div className="tickets-grid__item" key={ticket.ticket}>
            <div className={`card h-100 shadow ${isOverdue(ticket.dueDate) || daysOld(ticket.updated) >= 5 ? 'stale' : ''} ${priorityClass(ticket.priority)}`}>
              <div className={`card-header d-flex align-items-center justify-content-between ${isOverdue(ticket.dueDate) ? 'bg-danger text-white' : ''}`}>
                <span className="ticket-key text-truncate">
                  <i className={`${getIssueTypeIcon(ticket.issuetype)} me-2`}></i>
                  <a href={ticket.link} target="_blank" rel="noopener noreferrer" className="text-decoration-none text-reset">
                    {ticket.ticket}
                  </a>
                </span>
                <span className={`badge fs-6 ${isOverdue(ticket.dueDate) ? 'bg-light text-dark' : 'bg-secondary'}`}>
                  {ticket.statusName}
                </span>
              </div>
              <div className="card-body">
                <p className="card-text">{linkifyJiraKeys(ticket.title, baseUrl)}</p>
                {ticket.labels && ticket.labels.length > 0 && (
                  <p className="card-text">
                    {ticket.labels.map(label => (
                      <span key={label} className="badge bg-secondary me-1">{label}</span>
                    ))}
                  </p>
                )}
                {ticket.priority && (
                  <p className="card-text"><small className="text-muted">Priority: {ticket.priority}</small></p>
                )}
                {ticket.dueDate && (
                  <p className="card-text">
                    <small className="text-muted">Due: {ticket.dueDate} ({dueDeltaLabel(ticket.dueDate)})</small>
                  </p>
                )}
                <p className="card-text"><small className="text-muted">Updated: {timeAgo(ticket.updated)}</small></p>
                {ticket.latestComment ? (
                  <p className="card-text">
                    <small className="text-muted">Latest comment{ticket.latestComment.author ? ` by ${ticket.latestComment.author}` : ''} ({timeAgo(ticket.latestComment.created)}):</small>
                    <br />
                    <span className="latest-comment">{linkifyJiraKeys(ticket.latestComment.body || 'No comment body available.', baseUrl)}</span>
                  </p>
                ) : (
                  <p className="card-text"><small className="text-muted">No comments</small></p>
                )}
                <p className="card-text">
                  <img src={ticket.avatarUrl} alt={ticket.assignee} className="rounded-circle me-2" width="32" height="32" />
                  <small className="text-muted">Assignee: {ticket.assignee}</small>
                </p>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default TicketsList;
