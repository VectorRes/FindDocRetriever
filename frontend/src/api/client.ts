import type {
  AnswerResponse,
  AuditFilters,
  AuditLogPage,
  CellOut,
  CompareRequest,
  Comparison,
  Dashboard,
  DashboardCatalog,
  DocumentListOut,
  DocumentOut,
  DocumentResolutionOut,
  FolderNode,
  OAuthProvider,
  OAuthProviderInfo,
  QueryRequest,
  RatioReport,
  RatioRequest,
  RepositoryAccount,
  SourceTier,
  SyncedFile,
  SyncFailure,
  SyncOverview,
  SyncSource,
  SyncSourceRequest,
  SyncSourceUpdate,
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
  // ID-HU-BE-014: the pre-release disclosure group (sees "restricted:insider").
  "pre-release-disclosure",
  "admin",
] as const;
export type UserRole = (typeof USER_ROLES)[number];

// Roles that can read the access audit trail — mirrors ADMIN_ROLES in
// app/retrieval/access.py, which the backend enforces.
export const AUDITOR_ROLES: readonly UserRole[] = ["admin"];

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

// Who is asking, for the audit trail (ID-HU-BE-014). Like the role, it's
// taken at face value until real authentication exists.
const USER_ID_STORAGE_KEY = "findoc.userId";
const DEFAULT_USER_ID = "demo-user";
let currentUserId: string = readStoredUserId();

function readStoredUserId(): string {
  try {
    const stored = localStorage.getItem(USER_ID_STORAGE_KEY)?.trim();
    if (stored) return stored;
  } catch {
    // Storage can be unavailable (private mode, tests) — fall back to the default.
  }
  return DEFAULT_USER_ID;
}

export function getUserId(): string {
  return currentUserId;
}

export function setUserId(userId: string): void {
  currentUserId = userId.trim() || DEFAULT_USER_ID;
  try {
    localStorage.setItem(USER_ID_STORAGE_KEY, currentUserId);
  } catch {
    // Not persisting the choice is fine; it still applies for this session.
  }
}

function roleHeaders(): Record<string, string> {
  return { "X-User-Role": currentRole, "X-User-Id": currentUserId };
}

// FastAPI errors come as {"detail": "..."}: show the message, not the JSON.
export async function readErrorDetail(res: Response): Promise<string> {
  const body = await res.text().catch(() => "");
  try {
    const parsed = JSON.parse(body);
    if (typeof parsed?.detail === "string") return parsed.detail;
  } catch {
    // Not JSON (e.g. a proxy's HTML error page): fall through to the raw text.
  }
  return body;
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

// Loaded directly by the browser (iframe / link), so the role and user go in
// the query string instead of headers.
export function documentFileUrl(documentId: string): string {
  return (
    `${API_URL}/documents/${documentId}/file?role=${encodeURIComponent(currentRole)}` +
    `&user=${encodeURIComponent(currentUserId)}`
  );
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

// ID-HU-BE-016: needs a reviewer role, like approval.
export async function setDocumentSourceTier(documentId: string, tier: SourceTier): Promise<DocumentOut> {
  const res = await fetch(`${API_URL}/documents/${documentId}/source-tier`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...roleHeaders() },
    body: JSON.stringify({ tier }),
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
      const detail = await readErrorDetail(res);
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

// ID-HU-BE-010. One model call per ratio input (and per document tried), so
// calculating the whole library takes longer than a comparison.
const RATIOS_TIMEOUT_MS = 180_000;

export async function calculateRatios(request: RatioRequest): Promise<RatioReport> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), RATIOS_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_URL}/ratios`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...roleHeaders() },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new ApiError(detail || `Ratio calculation failed with status ${res.status}`, "http", res.status);
    }
    return (await res.json()) as RatioReport;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("The ratio calculation took too long to respond.", "timeout");
    }
    throw new ApiError("Could not reach the FinDoc Retriever API.", "network");
  } finally {
    clearTimeout(timeout);
  }
}

// ID-HU-BE-014: the access audit trail (admins only; anyone else gets 403).
function auditQuery(filters: AuditFilters, page?: { limit: number; offset: number }): string {
  const params = new URLSearchParams();
  if (filters.user_id?.trim()) params.set("user_id", filters.user_id.trim());
  if (filters.document?.trim()) params.set("document", filters.document.trim());
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.outcome === "granted") params.set("authorized", "true");
  if (filters.outcome === "denied") params.set("authorized", "false");
  if (filters.tier?.trim()) params.set("tier", filters.tier.trim());
  if (filters.insider_only) params.set("insider_only", "true");
  if (filters.resource) params.set("resource", filters.resource);
  if (page) {
    params.set("limit", String(page.limit));
    params.set("offset", String(page.offset));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function listAccessLogs(
  filters: AuditFilters,
  page: { limit: number; offset: number }
): Promise<AuditLogPage> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/audit/access-logs${auditQuery(filters, page)}`, { headers: roleHeaders() });
  } catch {
    throw new ApiError("Could not reach the FinDoc Retriever API.", "network");
  }
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new ApiError(detail || `Audit query failed with status ${res.status}`, "http", res.status);
  }
  return (await res.json()) as AuditLogPage;
}

export async function exportAccessLogs(filters: AuditFilters): Promise<Blob> {
  const res = await fetch(`${API_URL}/audit/access-logs/export.csv${auditQuery(filters)}`, {
    headers: roleHeaders(),
  });
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new ApiError(detail || `Export failed with status ${res.status}`, "http", res.status);
  }
  return res.blob();
}

// ID-HU-FE-004: the ratios dashboard. Reading it is fast (stored results);
// a refresh recalculates every period, one model call per input.
const DASHBOARD_REFRESH_TIMEOUT_MS = 600_000;

export function getDashboardCatalog(): Promise<DashboardCatalog> {
  return getJson(`/dashboard/catalog`);
}

export async function getDashboard(entity: string): Promise<Dashboard> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/dashboard?entity=${encodeURIComponent(entity)}`, { headers: roleHeaders() });
  } catch {
    throw new ApiError("Could not reach the FinDoc Retriever API.", "network");
  }
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new ApiError(detail || `Loading the dashboard failed with status ${res.status}`, "http", res.status);
  }
  return (await res.json()) as Dashboard;
}

export async function refreshDashboard(entity: string, periods?: string[]): Promise<Dashboard> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DASHBOARD_REFRESH_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_URL}/dashboard/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...roleHeaders() },
      body: JSON.stringify({ entity, periods: periods && periods.length ? periods : null }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const detail = await readErrorDetail(res);
      throw new ApiError(detail || `The calculation failed with status ${res.status}`, "http", res.status);
    }
    return (await res.json()) as Dashboard;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("The calculation took too long to respond.", "timeout");
    }
    throw new ApiError("Could not reach the FinDoc Retriever API.", "network");
  } finally {
    clearTimeout(timeout);
  }
}

// Excel export of the dashboard on screen (posted back, not recomputed).
export async function exportDashboard(dashboard: Dashboard): Promise<Blob> {
  const res = await fetch(`${API_URL}/dashboard/export.xlsx`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dashboard),
  });
  if (!res.ok) {
    throw new ApiError(`Export failed with status ${res.status}`, "http", res.status);
  }
  return res.blob();
}

// --- ID-HU-INT-001: repository sync and Google / Microsoft sign-in ---

async function sendJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method,
      headers: { "Content-Type": "application/json", ...roleHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Could not reach the FinDoc Retriever API.", "network");
  }
  if (!res.ok) {
    const detail = await readErrorDetail(res);
    throw new ApiError(detail || `Request failed with status ${res.status}`, "http", res.status);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export function getOAuthProviders(): Promise<OAuthProviderInfo[]> {
  return getJson("/oauth/providers");
}

// The provider's sign-in page; the caller sends the browser there.
export async function startSignIn(provider: OAuthProvider): Promise<string> {
  const { authorize_url } = await sendJson<{ authorize_url: string }>("GET", `/oauth/${provider}/start`);
  return authorize_url;
}

export function listRepositoryAccounts(): Promise<RepositoryAccount[]> {
  return sendJson("GET", "/repository-accounts");
}

export function disconnectRepositoryAccount(accountId: string): Promise<void> {
  return sendJson("DELETE", `/repository-accounts/${accountId}`);
}

export function browseFolders(accountId: string, parent?: string): Promise<FolderNode[]> {
  const query = parent ? `?parent=${encodeURIComponent(parent)}` : "";
  return sendJson("GET", `/repository-accounts/${accountId}/folders${query}`);
}

export function getSyncOverview(): Promise<SyncOverview> {
  return sendJson("GET", "/sync/sources");
}

export function connectFolder(request: SyncSourceRequest): Promise<SyncSource> {
  return sendJson("POST", "/sync/sources", request);
}

// ID-HU-INT-002: e.g. the folder's access rules.
export function updateFolder(sourceId: string, update: SyncSourceUpdate): Promise<SyncSource> {
  return sendJson("PATCH", `/sync/sources/${sourceId}`, update);
}

export function disconnectFolder(sourceId: string): Promise<void> {
  return sendJson("DELETE", `/sync/sources/${sourceId}`);
}

export function runSync(sourceId?: string): Promise<{ started: boolean }> {
  return sendJson("POST", `/sync/run${sourceId ? `?source_id=${sourceId}` : ""}`);
}

// ID-HU-INT-003: the sync failure log, newest first.
export function listSyncFailures(unresolved = false): Promise<SyncFailure[]> {
  return sendJson("GET", `/sync/failures${unresolved ? "?unresolved=true" : ""}`);
}

// ID-HU-INT-003: try a failed (or flagged) file again now.
export function retrySyncedFile(fileId: string): Promise<{ started: boolean }> {
  return sendJson("POST", `/sync/files/${fileId}/retry`);
}

export function listSyncedFiles(sourceId: string): Promise<SyncedFile[]> {
  return sendJson("GET", `/sync/sources/${sourceId}/files`);
}
