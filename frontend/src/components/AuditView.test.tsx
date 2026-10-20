import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AuditView, { toFilters } from "./AuditView";
import { ApiError, listAccessLogs } from "../api/client";
import type { AuditLog, AuditLogPage } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, listAccessLogs: vi.fn(), exportAccessLogs: vi.fn() };
});

const mockedList = vi.mocked(listAccessLogs);

function makeLog(overrides: Partial<AuditLog> = {}): AuditLog {
  return {
    id: crypto.randomUUID(),
    occurred_at: "2026-10-16T14:30:00Z",
    user_id: "ana",
    roles: ["analyst"],
    action: "retrieve",
    resource: "chat",
    document_id: "doc-1",
    document_filename: "Legal_Contingencies_FY24.pdf",
    chunk_id: "chunk-1",
    confidentiality_tag: "restricted:legal-contingencies",
    authorized: false,
    is_insider: false,
    question: "What is the litigation provision?",
    reason: "Requires legal-access.",
    request_id: "req-1",
    ...overrides,
  };
}

function page(items: AuditLog[], total = items.length, offset = 0): AuditLogPage {
  return { total, limit: 25, offset, items };
}

describe("AuditView", () => {
  beforeEach(() => {
    mockedList.mockReset();
  });

  it("lists granted and denied attempts with user, document, tier and time", async () => {
    mockedList.mockResolvedValue(
      page([
        makeLog(),
        makeLog({ user_id: "carlos", roles: ["financial-controller"], authorized: true, reason: null }),
      ])
    );
    render(<AuditView />);

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[1]).getByText("ana")).toBeInTheDocument();
    expect(within(rows[1]).getByText("denied")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Legal_Contingencies_FY24.pdf")).toBeInTheDocument();
    expect(within(rows[1]).getByText("restricted:legal-contingencies")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Requires legal-access.")).toBeInTheDocument();
    expect(within(rows[2]).getByText("granted")).toBeInTheDocument();
    expect(screen.getByText("1–2 of 2")).toBeInTheDocument();
  });

  it("marks pre-release insider attempts", async () => {
    mockedList.mockResolvedValue(page([makeLog({ confidentiality_tag: "restricted:insider", is_insider: true })]));
    render(<AuditView />);
    expect(await screen.findByText("insider")).toBeInTheDocument();
  });

  it("applies the filters for user, document and outcome", async () => {
    mockedList.mockResolvedValue(page([]));
    render(<AuditView />);
    await screen.findByText("No access attempts match these filters.");

    await userEvent.type(screen.getByLabelText("User"), "ana");
    await userEvent.type(screen.getByLabelText("Document"), "Legal_Contingencies");
    await userEvent.selectOptions(screen.getByLabelText("Outcome"), "denied");
    await userEvent.click(screen.getByLabelText("Pre-release insider only"));
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(2));
    const [filters, paging] = mockedList.mock.calls[1];
    expect(filters).toMatchObject({
      user_id: "ana",
      document: "Legal_Contingencies",
      outcome: "denied",
      insider_only: true,
    });
    expect(paging).toEqual({ limit: 25, offset: 0 });
  });

  it("pages through the trail", async () => {
    mockedList.mockResolvedValueOnce(page([makeLog()], 30, 0)).mockResolvedValueOnce(page([makeLog()], 30, 25));
    render(<AuditView />);
    await screen.findByText("1–25 of 30");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(mockedList.mock.calls[1][1]).toEqual({ limit: 25, offset: 25 }));
    expect(await screen.findByText("26–30 of 30")).toBeInTheDocument();
  });

  it("explains a 403 instead of showing an empty trail", async () => {
    mockedList.mockRejectedValue(new ApiError("Forbidden", "http", 403));
    render(<AuditView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("only available to administrators");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("toFilters", () => {
  it("turns the date inputs into whole days", () => {
    const filters = toFilters({
      user_id: "",
      document: "",
      from: "2026-10-01",
      to: "2026-10-16",
      outcome: "all",
      tier: "",
      insider_only: false,
      resource: "",
    });
    expect(new Date(filters.from!).getTime()).toBe(new Date("2026-10-01T00:00:00").getTime());
    expect(new Date(filters.to!).getTime()).toBe(new Date("2026-10-16T23:59:59.999").getTime());
    expect(filters.resource).toBeUndefined();
  });
});
