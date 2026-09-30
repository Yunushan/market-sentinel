import { useState } from "react";
import type { AlertEventHistory as EventHistory } from "./types.js";

const PAGE_SIZE = 50;

function eventTime(value: number): string {
  const date = new Date(value * 1000);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : "Unknown";
}

export function AlertEventHistory({ history, busyEventId, onAcknowledge, onRefresh }: {
  history?: EventHistory;
  busyEventId: string | null;
  onAcknowledge: (id: string) => void;
  onRefresh: () => void;
}) {
  const [includeAcknowledged, setIncludeAcknowledged] = useState(false);
  const [page, setPage] = useState(0);
  const events = (history?.events ?? [])
    .filter((event) => includeAcknowledged || event.acknowledged_at === 0)
    .sort((left, right) => right.created_at - left.created_at || left.id.localeCompare(right.id));
  const currentPage = Math.min(page, Math.max(0, Math.ceil(events.length / PAGE_SIZE) - 1));
  const offset = currentPage * PAGE_SIZE;
  return (
    <section aria-labelledby="alert-event-history-heading">
      <h2 id="alert-event-history-heading">Alert events</h2>
      <p className="muted-text">
        {history?.counts.unacknowledged ?? 0} awaiting acknowledgement. Events remain available after an alert is deleted or the worker restarts.
      </p>
      <button type="button" className="secondary-button" disabled={busyEventId !== null} onClick={onRefresh}>Refresh saved events</button>
      <label className="check-row">
        <input type="checkbox" checked={includeAcknowledged} onChange={(event) => {
          setIncludeAcknowledged(event.target.checked);
          setPage(0);
        }} />
        <span>Include acknowledged events</span>
      </label>
      <div role="region" aria-label="Saved alert events" className="alert-event-list">
            {events.slice(offset, offset + PAGE_SIZE).map((event) => (
              <article key={event.id} className="alert-event">
                <small>{eventTime(event.created_at)} · {event.market_id}:{event.contract_id}</small>
                <p>{event.message}</p>
                <div className="button-row">
                  <span>{event.acknowledged_at === 0 ? "Awaiting acknowledgement" : `Acknowledged ${eventTime(event.acknowledged_at)}`}</span>
                  {event.acknowledged_at === 0 ? (
                  <button type="button" className="secondary-button" disabled={busyEventId !== null}
                    onClick={() => onAcknowledge(event.id)} aria-label={`Acknowledge event for ${event.label}`}>
                    {busyEventId === event.id ? "Saving…" : "Acknowledge"}
                  </button>
                  ) : null}
                </div>
              </article>
            ))}
            {!events.length ? <p>No {includeAcknowledged ? "saved" : "unacknowledged"} alert events.</p> : null}
      </div>
      {events.length > PAGE_SIZE ? (
        <div className="button-row">
          <button type="button" className="secondary-button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous events</button>
          <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, events.length)} of {events.length}</span>
          <button type="button" className="secondary-button" disabled={offset + PAGE_SIZE >= events.length} onClick={() => setPage(currentPage + 1)}>Next events</button>
        </div>
      ) : null}
    </section>
  );
}
