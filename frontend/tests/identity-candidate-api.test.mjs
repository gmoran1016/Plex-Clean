import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/api.ts", import.meta.url), "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { api } = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString("base64")}`);

test("getIdentityCandidates encodes library and pagination with same-origin request defaults", async () => {
  const previousFetch = globalThis.fetch;
  let requestedUrl;
  let requestedInit;
  globalThis.fetch = async (url, init) => {
    requestedUrl = url;
    requestedInit = init;
    return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
  };

  try {
    assert.deepEqual(await api.getIdentityCandidates("Movies & TV", 50, 10), []);
    const parsed = new URL(requestedUrl, "http://localhost");
    assert.equal(parsed.pathname, "/api/identity-candidates");
    assert.equal(parsed.searchParams.get("library_id"), "Movies & TV");
    assert.equal(parsed.searchParams.get("offset"), "50");
    assert.equal(parsed.searchParams.get("limit"), "10");
    assert.equal(requestedInit.credentials, "same-origin");
    assert.equal(new Headers(requestedInit.headers).get("Accept"), "application/json");
  } finally {
    globalThis.fetch = previousFetch;
  }
});
