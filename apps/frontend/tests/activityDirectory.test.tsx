import { it, expect, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useActivityDirectory } from "../src/hooks/useActivityDirectory";
import { readDirectory, saveDirectory } from "../src/utils/activityDirectory";
import { apiFetch } from "../src/api/client";
vi.mock("../src/api/client", () => ({ apiFetch: vi.fn() }));
const alice = { rawAuthor: "Alice", displayName: "Alice" };
const bob = { rawAuthor: "Bob", displayName: "Bob" };
const empty: typeof alice[] = [];

it("restores the saved directory before authentication without fetching a summary", () => {
  saveDirectory("one", [alice]);
  vi.mocked(apiFetch).mockClear();
  const { result } = renderHook(() => useActivityDirectory("one", false, empty, false, vi.fn()));
  expect(result.current.directory).toEqual([alice]);
  expect(apiFetch).not.toHaveBeenCalled();
});

it("fetches independently on a cold start and ignores the previous account's late response", async () => {
  let resolveFirst!: (response: Response) => void;
  vi.mocked(apiFetch).mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve; }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ authors: [bob] })));
  const { result, rerender } = renderHook(({ email }) => useActivityDirectory(email, true, empty, false, vi.fn()), { initialProps: { email: "one" } });
  rerender({ email: "two" });
  await waitFor(() => expect(result.current.directory).toEqual([bob]));
  await act(async () => resolveFirst(new Response(JSON.stringify({ authors: [alice] }))));
  expect(result.current.directory).toEqual([bob]);
  expect(readDirectory("one")).toEqual([]);
});

it("a successful summary wins over a late directory response", async () => {
  let resolve!: (response: Response) => void;
  vi.mocked(apiFetch).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const { result, rerender } = renderHook(({ authors, ready }) => useActivityDirectory("one", true, authors, ready, vi.fn()),
    { initialProps: { authors: empty, ready: false } });
  rerender({ authors: [alice], ready: true });
  await act(async () => resolve(new Response(JSON.stringify({ authors: [bob] }))));
  expect(result.current.directory).toEqual([alice]);
});

it("clears identity storage and authentication on a confirmed 401", async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(new Response(null, { status: 401 }));
  const clear = vi.fn();
  renderHook(() => useActivityDirectory("one", true, empty, false, clear));
  await waitFor(() => expect(clear).toHaveBeenCalledOnce());
  expect(readDirectory("one")).toEqual([]);
});
