import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/api.ts", import.meta.url), "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { api, ApiError } = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString("base64")}`);

test("Plex rate-limit responses preserve their retry delay", async () => {
  const previousFetch = globalThis.fetch;
  const previousWindow = globalThis.window;
  globalThis.window = new EventTarget();
  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: "Plex temporarily rate-limited this request." }),
    { status: 429, headers: { "Content-Type": "application/json", "Retry-After": "45" } },
  );

  try {
    await assert.rejects(api.getAuthStatus(), (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 429);
      assert.equal(error.retryAfterSeconds, 45);
      return true;
    });
  } finally {
    globalThis.fetch = previousFetch;
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});
