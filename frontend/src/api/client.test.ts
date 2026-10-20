import { describe, expect, it, vi } from "vitest";
import { readErrorDetail } from "./client";

describe("readErrorDetail", () => {
  it("returns FastAPI's detail message instead of the JSON body", async () => {
    const res = new Response(JSON.stringify({ detail: "The language model service didn't respond." }), { status: 503 });
    expect(await readErrorDetail(res)).toBe("The language model service didn't respond.");
  });

  it("falls back to the raw body when it isn't a JSON detail", async () => {
    expect(await readErrorDetail(new Response("Bad Gateway", { status: 502 }))).toBe("Bad Gateway");
    expect(await readErrorDetail(new Response(JSON.stringify({ detail: [{ msg: "x" }] }), { status: 422 }))).toBe(
      '{"detail":[{"msg":"x"}]}'
    );
  });
});

describe("listAccessLogs", () => {
  it("sends the filters, the page and who is asking", async () => {
    const { listAccessLogs, setUserId, setUserRole } = await import("./client");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ total: 0, limit: 25, offset: 50, items: [] }), { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);
    setUserRole("admin");
    setUserId("auditor-1");

    await listAccessLogs(
      { user_id: " ana ", document: "Legal", outcome: "denied", insider_only: true, tier: "" },
      { limit: 25, offset: 50 }
    );

    const [url, init] = fetchMock.mock.calls[0];
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("user_id")).toBe("ana");
    expect(params.get("document")).toBe("Legal");
    expect(params.get("authorized")).toBe("false");
    expect(params.get("insider_only")).toBe("true");
    expect(params.has("tier")).toBe(false);
    expect(params.get("offset")).toBe("50");
    expect(init.headers).toMatchObject({ "X-User-Role": "admin", "X-User-Id": "auditor-1" });
    vi.unstubAllGlobals();
  });

  it("raises the API's message on 403", async () => {
    const { ApiError, listAccessLogs } = await import("./client");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Admins only." }), { status: 403 }))
    );
    await expect(listAccessLogs({}, { limit: 25, offset: 0 })).rejects.toMatchObject({
      message: "Admins only.",
      status: 403,
    });
    expect(ApiError).toBeDefined();
    vi.unstubAllGlobals();
  });
});
