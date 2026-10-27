import { useState } from 'react';
import { useWorkspaceData, type ClientSummary } from './management';

export function ClientChooser({ workspaceId, onChoose }: { workspaceId: string; onChoose: (client: ClientSummary) => void }) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const resource = useWorkspaceData<{ items: ClientSummary[]; has_more: boolean }>(
    `${workspaceId}/clients?page=${page}&search=${encodeURIComponent(search)}`,
  );

  return <section className="management-panel">
    <h2>Select a client</h2>
    <form className="management-inline-form" onSubmit={event => {
      event.preventDefault();
      setPage(1);
      setSearch(String(new FormData(event.currentTarget).get('search')));
    }}>
      <label>Search clients<input name="search" maxLength={200} /></label>
      <button type="submit">Search</button>
    </form>
    {resource.error ? <p role="alert">{resource.error} <button type="button" onClick={resource.reload}>Retry</button></p>
      : !resource.data ? <p role="status">Loading clients…</p> : <>
        {!resource.data.items.some(client => client.status === 'ACTIVE')
          ? <p>No active clients on this page. <a href="#/clients">Manage clients</a> or ask your firm administrator for an assignment.</p>
          : null}
        {resource.data.items.filter(client => client.status === 'ACTIVE').map(client =>
          <div className="management-row" key={client.id}>
            <div><strong>{client.name}</strong><small>{client.pan || 'Client workspace record'}</small></div>
            <button type="button" onClick={() => onChoose(client)}>Select {client.name}</button>
          </div>)}
        <div className="management-pager">
          <button type="button" disabled={page === 1} onClick={() => setPage(value => value - 1)}>Previous</button>
          <span>Page {page}</span>
          <button type="button" disabled={!resource.data.has_more} onClick={() => setPage(value => value + 1)}>Next</button>
        </div>
      </>}
  </section>;
}
