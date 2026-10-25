import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RepositoriesView from "./RepositoriesView";
import {
  browseFolders,
  connectFolder,
  getOAuthProviders,
  getSyncOverview,
  listRepositoryAccounts,
  listSyncedFiles,
  listSyncFailures,
  retrySyncedFile,
  runSync,
  startSignIn,
  updateFolder,
} from "../api/client";
import type { OAuthProviderInfo, RepositoryAccount, SyncOverview, SyncSource } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getOAuthProviders: vi.fn(),
    listRepositoryAccounts: vi.fn(),
    getSyncOverview: vi.fn(),
    startSignIn: vi.fn(),
    browseFolders: vi.fn(),
    connectFolder: vi.fn(),
    runSync: vi.fn(),
    listSyncedFiles: vi.fn(),
    disconnectFolder: vi.fn(),
    disconnectRepositoryAccount: vi.fn(),
    updateFolder: vi.fn(),
    listSyncFailures: vi.fn(),
    retrySyncedFile: vi.fn(),
  };
});

const PROVIDERS: OAuthProviderInfo[] = [
  { provider: "google", label: "Google Drive", kind: "google_drive", configured: true,
    redirect_uri: "http://localhost:8000/oauth/google/callback" },
  { provider: "microsoft", label: "SharePoint / OneDrive", kind: "sharepoint", configured: false,
    redirect_uri: "http://localhost:8000/oauth/microsoft/callback" },
];

const ACCOUNT: RepositoryAccount = {
  id: "acc-1", provider: "microsoft", email: "ana@contoso.com", display_name: "Ana",
  connected_by: "ana", created_at: "2026-10-24T10:00:00Z",
};

function source(overrides: Partial<SyncSource> = {}): SyncSource {
  return {
    id: "src-1", kind: "sharepoint", name: "Corporate Finance/Q3 Reports", config: {},
    confidentiality_tag: "internal", source_tier: null, auto_approve: true, enabled: true,
    created_at: "2026-10-24T10:00:00Z", last_sync_at: "2026-10-24T10:15:00Z", last_status: "ok",
    last_error: null, last_summary: { new: 1, updated: 1, unchanged: 3 }, file_count: 5,
    account_id: "acc-1", account_email: "ana@contoso.com", permission_rules: [], ...overrides,
  };
}

function overview(sources: SyncSource[] = [], extra: Partial<SyncOverview> = {}): SyncOverview {
  return { interval_minutes: 15, running: false, next_run_at: null, sources, needs_review: 0, open_failures: 0,
           retry_max_attempts: 5, ...extra };
}

describe("RepositoriesView", () => {
  const assign = vi.fn();
  const originalLocation = window.location;

  beforeEach(() => {
    vi.mocked(getOAuthProviders).mockResolvedValue(PROVIDERS);
    vi.mocked(listRepositoryAccounts).mockResolvedValue([ACCOUNT]);
    vi.mocked(getSyncOverview).mockResolvedValue(overview([source()]));
    vi.mocked(runSync).mockResolvedValue({ started: true });
    Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, assign } });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
    vi.clearAllMocks();
  });

  it("signs in with Google by going to the provider's page", async () => {
    vi.mocked(startSignIn).mockResolvedValue("https://accounts.google.com/o/oauth2/v2/auth?client_id=x");
    render(<RepositoriesView />);

    await userEvent.click(await screen.findByRole("button", { name: "Sign in with Google" }));

    expect(startSignIn).toHaveBeenCalledWith("google");
    expect(assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?client_id=x");
  });

  it("says how to set up a sign-in that isn't configured", async () => {
    render(<RepositoriesView />);
    expect(await screen.findByRole("button", { name: "Sign in with Microsoft" })).toBeDisabled();
    expect(screen.getByText("http://localhost:8000/oauth/microsoft/callback")).toBeInTheDocument();
  });

  it("browses the account's folders and connects one, then syncs it", async () => {
    vi.mocked(browseFolders)
      .mockResolvedValueOnce([{ id: "sites", name: "SharePoint sites", selectable: false, config: {}, browsable: true }])
      .mockResolvedValueOnce([
        { id: "item|d|cf", name: "Q3 Reports", selectable: true, config: { drive_id: "d", item_id: "cf" }, browsable: true },
      ]);
    vi.mocked(connectFolder).mockResolvedValue(source({ id: "src-2", name: "SharePoint sites/Q3 Reports" }));
    render(<RepositoriesView />);

    await userEvent.click(await screen.findByRole("button", { name: "Add folders" }));
    await userEvent.click(await screen.findByRole("button", { name: "Open SharePoint sites" }));
    expect(browseFolders).toHaveBeenLastCalledWith("acc-1", "sites");
    await userEvent.click(await screen.findByRole("button", { name: "Connect Q3 Reports" }));
    const form = screen.getByRole("form", { name: "Connect folder" });
    await userEvent.click(within(form).getByLabelText(/only holds approved documents/));
    await userEvent.click(within(form).getByRole("button", { name: "Connect and sync" }));

    expect(connectFolder).toHaveBeenCalledWith({
      kind: "sharepoint", name: "SharePoint sites/Q3 Reports",
      config: { drive_id: "d", item_id: "cf", include_subfolders: false },
      confidentiality_tag: "internal", auto_approve: true, account_id: "acc-1", permission_rules: [],
    });
    expect(runSync).toHaveBeenCalledWith("src-2");
    expect(await screen.findByText(/Connected "SharePoint sites\/Q3 Reports"/)).toBeInTheDocument();
  });

  it("shows each connected folder's last sync, errors and files", async () => {
    vi.mocked(getSyncOverview).mockResolvedValue(overview([
      source(),
      source({ id: "src-3", kind: "google_drive", name: "Finance/Management Reports", last_status: "error",
               last_error: "Aug_Report.xlsx: download failed", account_email: null }),
    ]));
    vi.mocked(listSyncedFiles).mockResolvedValue([
      { id: "sf-1", attempts: 0, next_retry_at: null, remote_id: "r1", path: "Q3_Consolidated_Report.xlsx", version_tag: "c2", remote_modified_at: null,
        document_id: "d1", status: "ingested", error: null, synced_at: "2026-10-24T10:15:00Z",
        permissions: ["Finance"], access_tag: "internal" },
    ]);
    render(<RepositoriesView />);

    expect(await screen.findByTestId("sync-status-src-1")).toHaveTextContent("1 new · 1 updated · 3 unchanged");
    expect(screen.getByRole("alert")).toHaveTextContent("Aug_Report.xlsx: download failed");
    expect(screen.getByText(/service credentials/)).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: "Sync now" })[0]);
    expect(runSync).toHaveBeenCalledWith("src-1");
    await userEvent.click(screen.getAllByRole("button", { name: "Files (5)" })[0]);
    const table = await screen.findByRole("table", { name: "Files of Corporate Finance/Q3 Reports" });
    expect(within(table).getByText("Q3_Consolidated_Report.xlsx")).toBeInTheDocument();
  });

  it("connects a folder whose access follows the repository's sharing (ID-HU-INT-002)", async () => {
    vi.mocked(browseFolders).mockResolvedValue([
      { id: "item|d|lg", name: "Legal", selectable: true, config: { drive_id: "d", item_id: "lg" }, browsable: true },
    ]);
    vi.mocked(connectFolder).mockResolvedValue(source({ id: "src-4" }));
    render(<RepositoriesView />);

    await userEvent.click(await screen.findByRole("button", { name: "Add folders" }));
    await userEvent.click(await screen.findByRole("button", { name: "Connect Legal" }));
    const form = screen.getByRole("form", { name: "Connect folder" });
    await userEvent.click(within(form).getByRole("button", { name: "+ Add rule" }));
    await userEvent.type(within(form).getByLabelText("Rule 1 group or user"), "Legal Corporate");
    await userEvent.selectOptions(within(form).getByLabelText("Rule 1 classification"), "restricted:legal");
    await userEvent.click(within(form).getByRole("button", { name: "Connect and sync" }));

    expect(connectFolder).toHaveBeenCalledWith(expect.objectContaining({
      permission_rules: [{ principal: "Legal Corporate", tag: "restricted:legal" }],
    }));
  });

  it("edits a connected folder's access rules and shows access changes and removals", async () => {
    vi.mocked(getSyncOverview).mockResolvedValue(overview([source({
      permission_rules: [{ principal: "Finance", tag: "internal" }],
      last_summary: { unchanged: 2, permissions_changed: 1, removed: 1 },
    })]));
    vi.mocked(updateFolder).mockResolvedValue(source());
    vi.mocked(listSyncedFiles).mockResolvedValue([
      { id: "sf-1", attempts: 0, next_retry_at: null, remote_id: "r1", path: "Draft_Budget_2023.xlsx", version_tag: "c1", remote_modified_at: null, document_id: "d1",
        status: "removed", error: null, synced_at: "2026-10-25T10:00:00Z", permissions: [], access_tag: "internal" },
      { id: "sf-2", attempts: 0, next_retry_at: null, remote_id: "r2", path: "Legal_Contingencies_FY24.pdf", version_tag: "c1", remote_modified_at: null,
        document_id: "d2", status: "ingested", error: null, synced_at: "2026-10-25T10:00:00Z",
        permissions: ["Legal Corporate"], access_tag: "restricted:legal" },
    ]);
    render(<RepositoriesView />);

    expect(await screen.findByTestId("sync-status-src-1")).toHaveTextContent("1 access changed · 1 removed");
    await userEvent.click(screen.getByRole("button", { name: "Access rules (1)" }));
    const editor = screen.getByLabelText("Access rules of Corporate Finance/Q3 Reports");
    await userEvent.selectOptions(within(editor).getByLabelText("Rule 1 classification"), "restricted");
    await userEvent.click(within(editor).getByRole("button", { name: "Save access rules" }));
    expect(updateFolder).toHaveBeenCalledWith("src-1", { permission_rules: [{ principal: "Finance", tag: "restricted" }] });

    await userEvent.click(screen.getByRole("button", { name: "Files (5)" }));
    const table = await screen.findByRole("table", { name: "Files of Corporate Finance/Q3 Reports" });
    expect(within(table).getByText("removed from the repository")).toBeInTheDocument();
    expect(within(table).getByText("restricted:legal")).toHaveAttribute("title", "Shared with: Legal Corporate");
  });

  it("flags files that need manual review and retries one (ID-HU-INT-003)", async () => {
    vi.mocked(getSyncOverview).mockResolvedValue(overview(
      [source({ last_summary: { new: 19, failed: 1, needs_review: 1 } })],
      { needs_review: 1, open_failures: 2 },
    ));
    vi.mocked(listSyncedFiles).mockResolvedValue([
      { id: "sf-1", remote_id: "r1", path: "Corrupted.xlsx", version_tag: "c1", remote_modified_at: null,
        document_id: null, status: "needs_review", error: "ingestion: File is not a zip file",
        synced_at: "2026-10-25T10:00:00Z", permissions: [], access_tag: null, attempts: 5, next_retry_at: null },
      { id: "sf-2", remote_id: "r2", path: "Aug_Report.xlsx", version_tag: "c1", remote_modified_at: null,
        document_id: null, status: "failed", error: "download: timed out", synced_at: "2026-10-25T10:00:00Z",
        permissions: [], access_tag: null, attempts: 2, next_retry_at: "2026-10-25T10:30:00Z" },
    ]);
    vi.mocked(retrySyncedFile).mockResolvedValue({ started: true });
    render(<RepositoriesView />);

    expect(await screen.findByText(/1 file couldn't be synced after 5 attempts/)).toBeInTheDocument();
    expect(screen.getByTestId("sync-status-src-1")).toHaveTextContent("19 new · 1 failed · 1 need manual review");
    await userEvent.click(screen.getByRole("button", { name: "Files (5)" }));
    const table = await screen.findByRole("table", { name: "Files of Corporate Finance/Q3 Reports" });
    expect(within(table).getByText(/needs manual review after 5 attempts/)).toBeInTheDocument();
    expect(within(table).getByText(/failed \(attempt 2\/5\): download: timed out — retry/)).toBeInTheDocument();

    await userEvent.click(within(table).getByRole("button", { name: "Retry Corrupted.xlsx" }));
    expect(retrySyncedFile).toHaveBeenCalledWith("sf-1");
  });

  it("shows the sync failure log", async () => {
    vi.mocked(getSyncOverview).mockResolvedValue(overview([source()], { open_failures: 1 }));
    vi.mocked(listSyncFailures).mockResolvedValue([
      { id: "x1", source_id: "src-1", source_name: "Q3 Reports", remote_id: "r1", path: "Board_Pack.xlsx",
        attempt: 5, stage: "download", error: "503 Service Unavailable", occurred_at: "2026-10-25T10:00:00Z",
        next_retry_at: null, needs_review: true, resolved_at: null, resolution: null },
      { id: "x2", source_id: "src-1", source_name: "Q3 Reports", remote_id: "r2", path: "Aug_Report.xlsx",
        attempt: 1, stage: "download", error: "timed out", occurred_at: "2026-10-25T09:00:00Z",
        next_retry_at: null, needs_review: false, resolved_at: "2026-10-25T09:20:00Z", resolution: "retry succeeded" },
    ]);
    render(<RepositoriesView />);

    await userEvent.click(await screen.findByRole("button", { name: "Show log (1 open)" }));
    expect(listSyncFailures).toHaveBeenCalledWith(true);
    const log = await screen.findByRole("table", { name: "Failures" });
    expect(within(log).getByText("⚠ needs manual review")).toBeInTheDocument();
    expect(within(log).getByText("resolved: retry succeeded")).toBeInTheDocument();
  });

  it("reports the result of coming back from the sign-in", async () => {
    window.history.replaceState(null, "", "/?oauth=connected&provider=google&account=ana%40example.com#repositories");
    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
    render(<RepositoriesView />);
    expect(await screen.findByRole("status")).toHaveTextContent("Signed in to Google Drive as ana@example.com");
    await waitFor(() => expect(window.location.search).toBe(""));
  });
});
