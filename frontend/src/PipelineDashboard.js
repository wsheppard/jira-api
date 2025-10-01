import React from 'react';

function PipelineDashboard({ data, categories }) {
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

  const envDisplay = (env) => {
    return env.split('/')[0].toUpperCase();
  };

  const badgeClass = (result) => {
    const res = (result || '').toUpperCase();
    switch (res) {
      case 'SUCCESSFUL':
      case 'COMPLETED':
        return 'badge bg-success';
      case 'FAILED':
      case 'ERROR':
      case 'FAILED_WITH_ERRORS':
        return 'badge bg-danger';
      case 'STOPPED':
      case 'CANCELLED':
        return 'badge bg-secondary';
      case 'IN_PROGRESS':
        return 'badge bg-warning text-dark';
      default:
        return 'badge bg-info';
    }
  };

  const latestSuccessful = (repo, env) => {
    const runs = (data[repo]?.[env] ?? []);
    for (const run of runs) {
      const res = (run.result || '').toUpperCase();
      if (res === 'SUCCESSFUL' || res === 'COMPLETED') {
        return run;
      }
    }
    return null;
  };

  return (
    <div>
      <div className="table-responsive mb-4">
        <table className="table table-striped table-sm align-middle">
          <thead className="table-light">
            <tr>
              <th scope="col">Environment</th>
              <th scope="col">Frontend</th>
              <th scope="col">Backend</th>
            </tr>
          </thead>
          <tbody>
            {categories.map(env => {
              const frontend = latestSuccessful('frontend', env);
              const backend = latestSuccessful('backend', env);
              return (
                <tr key={env}>
                  <td>{envDisplay(env)}</td>
                  <td>
                    {frontend ? (
                      <>
                        <span>{frontend.ref_name}</span>
                        <small className="text-muted"> ({timeAgo(frontend.completed_on)})</small>
                      </>
                    ) : '-'}
                  </td>
                  <td>
                    {backend ? (
                      <>
                        <span>{backend.ref_name}</span>
                        <small className="text-muted"> ({timeAgo(backend.completed_on)})</small>
                      </>
                    ) : '-'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {Object.keys(data).map(repo => (
        <div className="mb-5" key={repo}>
          <h2 className="mt-4">{repo}</h2>
          {categories.map(env => (
            <div className="mb-4" key={env}>
              <h3>{envDisplay(env)}</h3>
              <div className="table-responsive">
                <table className="table table-striped table-sm align-middle">
                  <thead className="table-light">
                    <tr>
                      <th scope="col">Result</th>
                      <th scope="col">Ref Name</th>
                      <th scope="col">Commit</th>
                      <th scope="col">Completed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data[repo][env] || []).map((run, index) => (
                      <tr key={run.uuid || index}>
                        <td><span className={badgeClass(run.result)}>{run.result}</span></td>
                        <td>
                          <a href={run.pipeline_link} className="ref-link" target="_blank" rel="noopener noreferrer">
                            {run.ref_name}
                          </a>
                        </td>
                        <td><a href={run.commit_link} target="_blank" rel="noopener noreferrer">{run.commit}</a></td>
                        <td>{timeAgo(run.completed_on)}</td>
                      </tr>
                    ))}
                    {(data[repo][env] || []).length === 0 && (
                      <tr>
                        <td colSpan="4">-</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export default PipelineDashboard;
