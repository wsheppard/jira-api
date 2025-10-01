import React, { useState, useEffect } from 'react';
import TicketsList from './TicketsList';
import PipelineDashboard from './PipelineDashboard';
import './App.css';

function App() {
  const [openTickets, setOpenTickets] = useState([]);
  const [inProgressTickets, setInProgressTickets] = useState([]);
  const [pipelineData, setPipelineData] = useState({ frontend: {}, backend: {} });
  const [pipelineCategories, setPipelineCategories] = useState([]);

  useEffect(() => {
    fetch('/open-issues-by-due')
      .then(res => res.json())
      .then(data => setOpenTickets(data))
      .catch(err => console.error('Failed to load open tickets:', err));

    fetch('/in-progress')
      .then(res => res.json())
      .then(data => setInProgressTickets(data))
      .catch(err => console.error('Failed to load in-progress tickets:', err));

    fetch('/pipeline-dashboard')
      .then(res => res.json())
      .then(data => {
        setPipelineData(data);
        const repos = Object.keys(data);
        if (repos.length > 0) {
          setPipelineCategories(Object.keys(data[repos[0]]));
        }
      })
      .catch(err => console.error('Failed to load pipeline dashboard:', err));
  }, []);

  return (
    <div className="container my-4">
      <h1>Open Tickets by Due Date</h1>
      <TicketsList tickets={openTickets} />
      <h1>In Progress Tickets</h1>
      <TicketsList tickets={inProgressTickets} />
      <h1 className="mt-5">Latest Tag per Environment</h1>
      <h1 className="mt-5">Pipeline Dashboard</h1>
      <PipelineDashboard data={pipelineData} categories={pipelineCategories} />
    </div>
  );
}

export default App;