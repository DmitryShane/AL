import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { usePublisherAuthors } from "../src/components/settings/tabs/publisherProfiles/usePublisherAuthors";
import * as api from "../src/components/settings/tabs/publisherProfiles/api";
vi.mock("../src/components/settings/tabs/publisherProfiles/api");
const data = { publisherProfiles: [], deviceProfiles: [], authorProfiles: [], authorLinkJob: null };
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.loadPublisherProfiles).mockResolvedValue(data); });

it("does not request protected settings for viewers", () => {
  renderHook(() => usePublisherAuthors(vi.fn(), false));
  expect(api.loadPublisherProfiles).not.toHaveBeenCalled();
});

it("assigns an author and refreshes the dashboard after background completion", async () => {
  const onSaved = vi.fn();
  vi.mocked(api.assignPublisherAuthor).mockResolvedValue("job-1");
  vi.mocked(api.publisherAuthorJob).mockResolvedValue({ jobId: "job-1", status: "completed", phase: "Done", progress: 100 });
  const { result } = renderHook(() => usePublisherAuthors(onSaved));
  await waitFor(() => expect(api.loadPublisherProfiles).toHaveBeenCalled());
  await act(async () => { await result.current.assign("Publisher", "Alice", true); });
  await waitFor(() => expect(api.publisherAuthorJob).toHaveBeenCalledWith("job-1"));
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(2));
  expect(api.assignPublisherAuthor).toHaveBeenCalledWith("Publisher", "Alice", true);
});

it("restores an incomplete job on remount and retries it", async () => {
  const job = { jobId: "failed", status: "failed" as const, phase: "Rebuilding", progress: 0, error: "Interrupted" };
  vi.mocked(api.loadPublisherProfiles).mockResolvedValue({ ...data, authorLinkJob: job });
  vi.mocked(api.retryPublisherAuthorJob).mockResolvedValue("retry");
  vi.mocked(api.publisherAuthorJob).mockResolvedValue({ ...job, jobId: "retry", status: "completed", progress: 100 });
  const { result } = renderHook(() => usePublisherAuthors(vi.fn()));
  await waitFor(() => expect(result.current.job?.status).toBe("failed"));
  vi.mocked(api.loadPublisherProfiles).mockResolvedValue(data);
  await act(async () => { await result.current.retry(); });
  expect(api.retryPublisherAuthorJob).toHaveBeenCalledWith("failed");
  await waitFor(() => expect(result.current.busy).toBe(false));
});
