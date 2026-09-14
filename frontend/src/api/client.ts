import type { AnswerResponse, CellOut, DocumentResolutionOut, QueryRequest } from "./types";

const API_URL = import.meta.env.VITE_API_URL;
const ANSWER_TIMEOUT_MS = 30_000;

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
  const res = await fetch(`${API_URL}${path}`);
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

export function documentFileUrl(documentId: string): string {
  return `${API_URL}/documents/${documentId}/file`;
}

export async function postQueryAnswer(request: QueryRequest): Promise<AnswerResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), ANSWER_TIMEOUT_MS);

  try {
    const res = await fetch(`${API_URL}/query/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
