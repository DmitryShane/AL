import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ActivityPage } from "../src/pages/ActivityPage";
import { emptyActivitySummary } from "../src/utils/dashboardStorage";
import { clearDashboardCaches } from "../src/utils/browserStorage";
import { readDirectory, saveDirectory, clearDirectory } from "../src/utils/activityDirectory";
import type { AuthorRow } from "../src/types/dashboard";
vi.mock("../src/api/client", () => ({ apiFetch: () => new Promise(() => {}), API_URL: "", IS_LOCAL_DASHBOARD: true }));
const identity = { rawAuthor: "Alice", displayName: "Alice", team: "Core" };
const dateRange = { startDate: "2026-09-04", endDate: "2026-09-04", preset: "custom" as const };
const props = { summary: emptyActivitySummary, directory: [identity], dateRange, datePickerValue: dateRange,
  onDatePickerChange: vi.fn(), selectedAuthor: "Alice", authorSelectionError: null, setSelectedAuthor: vi.fn(),
  loading: true, refreshing: false, onRefreshAuthor: vi.fn() };
const author = { ...identity, ...emptyActivitySummary.totals, activeSeconds: 3600, productivity: 50, activityMix: [], savedPrefabs: [] } as AuthorRow;

describe("Activity presentation", () => {
  it("renders identities and real card structure before the summary arrives", () => {
    const { container } = render(<ActivityPage {...props} />);
    expect(screen.getAllByText("Alice").length).toBeGreaterThan(0);
    for (const label of ["Hourly Activity", "Activity Mix", "Worked Files", "Productivity"]) expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Loading data…").length).toBeGreaterThan(5);
    expect(container.querySelector(".authors-table-head")).not.toBeNull();
    expect(container.querySelector("#plugin-reports")).not.toBeNull();
    expect(container.textContent).not.toContain("0.00%");
    expect(container.textContent).not.toContain("Selected author is not available");
  });
  it("keeps card nodes and successful same-period values through preparation and errors", () => {
    const { container, rerender } = render(<ActivityPage {...props} loading={false} summary={{ ...emptyActivitySummary, authors: [author] }} />);
    const metrics = container.querySelector(".activity-grid");
    rerender(<ActivityPage {...props} dataError="Network unavailable" />);
    expect(container.querySelector(".activity-grid")).toBe(metrics);
    expect(metrics?.textContent).toContain("50.00%");
    expect(screen.getAllByText("Network unavailable").length).toBeGreaterThan(0);
    rerender(<ActivityPage {...props} summary={{ ...emptyActivitySummary, snapshot: { status: "preparing", date: dateRange.startDate } as never }} />);
    expect(container.querySelector(".activity-grid")).toBe(metrics);
    expect(metrics?.textContent).toContain("50.00%");
  });
  it("does not display another author or period's metrics", () => {
    const { container, rerender } = render(<ActivityPage {...props} loading={false} summary={{ ...emptyActivitySummary, authors: [author] }} />);
    rerender(<ActivityPage {...props} selectedAuthor="Bob" />);
    expect(container.querySelector(".activity-grid")?.textContent).not.toContain("50.00%");
    rerender(<ActivityPage {...props} summary={{ ...emptyActivitySummary, authors: [author] }} datePickerValue={{ ...dateRange, startDate: "2026-09-03", endDate: "2026-09-03" }} />);
    expect(container.querySelector(".activity-grid")?.textContent).not.toContain("50.00%");
  });
  it("keeps structure on empty history and with no cached identities", () => {
    const { container, rerender } = render(<ActivityPage {...props} directory={[]} selectedAuthor={null} />);
    expect(screen.getByText("Loading authors…")).toBeTruthy();
    const metrics = container.querySelector(".activity-grid");
    rerender(<ActivityPage {...props} loading={false} summary={{ ...emptyActivitySummary, snapshot: { status: "empty" } as never }} />);
    expect(container.querySelector(".activity-grid")).toBe(metrics);
    expect(screen.getAllByText("No activity data").length).toBeGreaterThan(0);
  });
  it("stores identities without metrics, isolates accounts and clears on logout", () => {
    saveDirectory("one@example.com", [author]);
    clearDashboardCaches();
    expect(readDirectory("one@example.com")).toEqual([identity]);
    expect(readDirectory("two@example.com")).toEqual([]);
    expect(localStorage.getItem("AL.Activity.Directory.v1")).not.toContain("activeSeconds");
    clearDirectory();
    expect(readDirectory("one@example.com")).toEqual([]);
  });
});
