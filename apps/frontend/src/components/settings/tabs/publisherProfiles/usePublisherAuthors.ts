import { useCallback, useEffect, useRef, useState } from "react";
import { assignPublisherAuthor, loadPublisherProfiles, publisherAuthorJob, retryPublisherAuthorJob } from "./api";
import type { PublisherAuthor, PublisherAuthorJob, PublisherProfile } from "./types";

export function usePublisherAuthors(onSaved: () => void, enabled = true) {
  const [profiles, setProfiles] = useState<PublisherProfile[]>([]);
  const [authors, setAuthors] = useState<PublisherAuthor[]>([]);
  const [job, setJob] = useState<PublisherAuthorJob | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const saved = useRef(onSaved);
  saved.current = onSaved;
  const refresh = useCallback(async () => {
    if (!enabled) return;
    const data = await loadPublisherProfiles();
    setProfiles(data.publisherProfiles);
    setAuthors(data.authorProfiles);
    setJob(data.authorLinkJob);
  }, [enabled]);
  useEffect(() => { void refresh().catch(e => setError(String(e))); }, [refresh]);
  useEffect(() => {
    if (job?.status !== "running") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await publisherAuthorJob(job.jobId);
        if (cancelled) return;
        if (!next) throw new Error("Rebuild job not found");
        setError("");
        if (next.status !== "running") {
          await refresh();
          setJob(next);
          saved.current();
          return;
        }
        setJob(next);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
      if (!cancelled) timer = setTimeout(() => void poll(), 1500);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [job?.jobId, job?.status, refresh]);
  async function run(action: () => Promise<string>) {
    setSaving(true);
    setError("");
    try {
      const jobId = await action();
      setJob({ jobId, status: "running", phase: "Queued", progress: 1 });
      const data = await loadPublisherProfiles();
      setProfiles(data.publisherProfiles);
      setAuthors(data.authorProfiles);
      saved.current();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  }
  return {
    profiles, authors, job, error, refresh,
    busy: saving || job?.status === "running",
    assign: (publisher: string, author: string, linked: boolean) => run(() => assignPublisherAuthor(publisher, author, linked)),
    retry: () => job ? run(() => retryPublisherAuthorJob(job.jobId)) : Promise.resolve(),
  };
}
export type PublisherAuthorsController = ReturnType<typeof usePublisherAuthors>;
