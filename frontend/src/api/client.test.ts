import { describe, expect, it } from "vitest";
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
