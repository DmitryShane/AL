import type { PublisherAuthorsController } from "./usePublisherAuthors";

export function PublisherAuthorJobStatus({ controller }: { controller: PublisherAuthorsController }) {
  const { job, error, retry, busy } = controller;
  return <div aria-live="polite">
    {error && <p className="notice error">{error}</p>}
    {job?.status === "running" && <p>Updating publisher activity: {job.phase} ({job.progress}%)</p>}
    {job?.status === "failed" && <p className="notice error">
      Activity history update is incomplete. {job.error}{" "}
      <button type="button" disabled={busy} onClick={() => void retry()}>Retry update</button>
    </p>}
  </div>;
}
