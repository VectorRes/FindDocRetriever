import type {
  AnswerResponse,
  CellOut,
  CompareRequest,
  Comparison,
  DocumentListOut,
  DocumentOut,
  DocumentResolutionOut,
  QueryRequest,
} from "./types";

const API_URL = import.meta.env.VITE_API_URL;
const ANSWER_TIMEOUT_MS = 30_000;

// Roles understood by the backend's access policy (app/retrieval/access.py).
// There's no auth yet: the chosen role is sent as X-User-Role and taken at
// face value by the API.
export const USER_ROLES = [
  "analyst",
  "reviewer",
  "financial-controller",
  "restricted-reviewer",
  "admin",
] as const;
export type UserRole = (typeof USER_ROLES)[number];

// Roles allowed to reclassify documents in the UI. The backend doesn't
// enforce this yet — it's a UI-level guard until real auth exists.
export const CLASSIFIER_ROLES: readonly UserRole[] = ["reviewer", "restricted-reviewer", "admin"];

// Roles allowed to approve a document version — mirrors APPROVER_ROLES in
// app/versioning.py, which the backend does enforce.
export const APPROVER_ROLES: readonly UserRole[] = [
  "reviewer",
  "financial-controller",
  "restricted-reviewer",
  "admin",
];

const ROLE_STORAGE_KEY = "findoc.userRole";
let currentRole: UserRole = readStoredRole();

function readStoredRole(): UserRole {
  try {
    const stored = localStorage.getItem(ROLE_STORAGE_KEY);
    if (stored && (USER_ROLES as readonly string[]).includes(stored)) return stored as UserRole;
  } catch {
    // Storage can be unavailable (private mode, tests) — fall back to the default.
  }
  return "analyst";
}

export function getUserRole(): UserRole {
  return currentRole;
}

export function setUserRole(role: UserRole): void {
  currentRole = role;
  try {
    localStorage.setItem(ROLE_STORAGE_KEY, role);
  } catch {
    // Not persisting the choice is fine; it still applies for this session.
  }
}

function roleHeaders(): Record<string, string> {
  return { "X-User-Role": currentRole };
}

export class ApiError extends Error {
  kind: "timeout" | "http" | "network";
  status?: number;

  constructor(message: string, kind: "timeout" | "http" | "network", status?: number) {
    super(message);
    this.kind = kind;
    this.status = status;
  }
}

export async function getHealth(): Promise<{ status: string }> {
  const res = await fetch(`${API_URL}/health`);
  if (!res.ok) {
    throw new ApiError(`Health check failed: ${res.status}`, "http", res.status);
  }
  return res.json();
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { headers: roleHeaders() });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(detail || `Request failed with status ${res.status}`, "http", res.status);
  }
  return (await res.json()) as T;
}

export function resolveDocument(documentId: string): Promise<DocumentResolutionOut> {
  return getJson(`/documents/${documentId}/resolve`);
}

export function getCell(documentId: string, sheetName: string, address: string): Promise<CellOut> {
  return getJson(
    `/documents/${documentId}/cells/${encodeURIComponent(sheetName)}/${encodeURIComponent(address)}`
  );
}

// Loaded directly by the browser (iframe / link), so the role goes in the
// query string instead of a header.
export function documentFileUrl(documentId: string): string {
  return `${API_URL}/documents/${documentId}/file?role=${encodeURIComponent(currentRole)}`;
}

export function listDocuments(): Promise<DocumentListOut> {
  return getJson(`/documents`);
}

export async function deleteDocument(documentId: string): Promise<void> {
  const res = await fetch(`${API_URL}/documents/${documentId}`, {
    method: "DELETE",
    headers: roleHeaders(),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(detail || `Delete failed with status ${res.status}`, "http", res.status);
  }
}

export async function setDocumentConfidentiality(documentId: string, tag: string): Promise<DocumentOut> {
  const res = await fetch(`${API_URL}/documents/${documentId}/confidentiality`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...roleHeaders() },
    body: JSON.stringify({ tag }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(detail || `Update failed with status ${res.status}`, "http", res.status);
  }
  return (await res.json()) as DocumentOut;
}

export async function approveDocument(documentId: string): Promise<DocumentOut> {
  const res = await fetch(`${API_URL}/documents/${documentId}/approve`, {
    method: "POST",
    headers: roleHeaders(),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(detail || `Approval failed with status ${res.status}`, "http", res.status);
  }
  return (await res.json()) as DocumentOut;
}

export async function uploadDocument(file: File): Promise<DocumentOut> {
  const body = new FormData();
  body.append("file", file);

  let res: Response;
  try {
    res = await fetch(`${API_URL}/ingest`, { method: "POST", body, headers: roleHeaders() });
  } catch {
    throw new ApiError("Could not reach the FinDoc Retriever API.", "network");
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(detail || `Upload failed with status ${res.status}`, "http", res.status);
  }

  return (await res.json()) as DocumentOut;
}

export async function postQueryAnswer(request: QueryRequest): Promise<AnswerResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), ANSWER_TIMEOUT_MS);

  try {
    const res = await fetch(`${API_URL}/query/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...roleHeaders() },
      body: JSON.stringify(request),
      signal: controller.signal,
    });

    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new ApiError(
        detail || `Request failed with status ${res.status}`,
        "http",
        res.status
      );
    }

    return (await res.json()) as AnswerResponse;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("The request took too long to respond.", "timeout");
    }
    throw new ApiError("Could not reach the FinDoc Retriever API.", "network");
  } finally {
    clearTimeout(timeout);
  }
}

// ID-HU-FE-003. One model call per document, so allow more time than a single answer.
const COMPARE_TIMEOUT_MS = 90_000;

export async function compareDocuments(request: CompareRequest): Promise<Comparison> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), COMPARE_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_URL}/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...roleHeaders() },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new ApiError(detail || `Comparison failed with status ${res.status}`, "http", res.status);
    }
    return (await res.json()) as Comparison;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("The comparison took too long to respond.", "timeout");
    }
    throw new ApiError("Could not reach the FinDoc Retriever API.", "network");
  } finally {
    clearTimeout(timeout);
  }
}

// Excel export of the comparison on screen (posted back, not recomputed).
export async function exportComparison(comparison: Comparison): Promise<Blob> {
  const res = await fetch(`${API_URL}/compare/export.xlsx`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(comparison),
  });
  if (!res.ok) {
    throw new ApiError(`Export failed with status ${res.status}`, "http", res.status);
  }
  return res.blob();
}
