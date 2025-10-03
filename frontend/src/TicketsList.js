import React from 'react';

function TicketsList({ tickets }) {
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

  const priorityClass = (priority) => {
    if (!priority) return '';
    const priorityValue = priority.toLowerCase();
    if (priorityValue === 'high') return 'priority-high';
    if (priorityValue === 'medium') return 'priority-medium';
    if (priorityValue === 'low') return 'priority-low';
    return '';
  };

  return (
    <div className="row row-cols-1 row-cols-md-3 g-4 mb-5">
      {tickets.map(ticket => (
        <div className="col" key={ticket.ticket}>
          <a href={ticket.link} target="_blank" rel="noopener noreferrer" className="text-decoration-none text-body">
            <div className={`card h-100 shadow ${isOverdue(ticket.dueDate) || daysOld(ticket.updated) >= 5 ? 'stale' : ''} ${priorityClass(ticket.priority)}`}>
              <div className={`card-header ${isOverdue(ticket.dueDate) ? 'bg-danger text-white' : ''}`}>
                <span className="ticket-key d-block text-truncate">{ticket.ticket}</span>
              </div>
              <div className="card-body">
                <p className="card-text">{ticket.title}</p>
                {ticket.priority && (
                  <p className="card-text"><small className="text-muted">Priority: {ticket.priority}</small></p>
                )}
                {ticket.dueDate && (
                  <p className="card-text"><small className="text-muted">Due: {ticket.dueDate}</small></p>
                )}
                <p className="card-text"><small className="text-muted">Updated: {timeAgo(ticket.updated)}</small></p>
                <p className="card-text">
                  <img src={ticket.avatarUrl} alt={ticket.assignee} className="rounded-circle me-2" width="32" height="32" />
                  <small className="text-muted">Assignee: {ticket.assignee}</small>
                </p>
              </div>
            </div>
          </a>
        </div>
      ))}
    </div>
  );
}

export default TicketsList;
