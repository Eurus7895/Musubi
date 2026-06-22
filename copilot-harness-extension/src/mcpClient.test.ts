import { test } from "node:test";
import assert from "node:assert/strict";
import { PassThrough } from "node:stream";

import { McpClient } from "./mcpClient";

function makeClient(): { client: McpClient; stdout: PassThrough; stdin: PassThrough } {
  const stdout = new PassThrough();
  const stdin = new PassThrough();
  const client = McpClient._forTest({
    stdin,
    stdout,
    kill: () => {},
  });
  return { client, stdout, stdin };
}

test("notification fan-out: server-style notification fires onNotification handlers", () => {
  const { client } = makeClient();
  const seen: Array<{ method: string; params: unknown }> = [];
  client.onNotification((method, params) => seen.push({ method, params }));

  client._handleLine(JSON.stringify({
    jsonrpc: "2.0",
    method: "subagent_spawned",
    params: { handle_id: "abc" },
  }));

  assert.equal(seen.length, 1);
  assert.equal(seen[0].method, "subagent_spawned");
  assert.deepEqual(seen[0].params, { handle_id: "abc" });
});

test("notification fan-out: missing method field is silently ignored (no crash, no emit)", () => {
  const { client } = makeClient();
  let count = 0;
  client.onNotification(() => { count += 1; });

  client._handleLine(JSON.stringify({ jsonrpc: "2.0", params: {} }));
  assert.equal(count, 0);
});

test("notification fan-out: malformed JSON does not throw or emit", () => {
  const { client } = makeClient();
  let count = 0;
  client.onNotification(() => { count += 1; });

  assert.doesNotThrow(() => client._handleLine("{not valid json"));
  assert.equal(count, 0);
});

test("notification fan-out: response messages (with id) are NOT delivered as notifications", () => {
  const { client } = makeClient();
  let count = 0;
  client.onNotification(() => { count += 1; });

  client._handleLine(JSON.stringify({ jsonrpc: "2.0", id: 1, result: {} }));
  assert.equal(count, 0);
});

test("notification fan-out: unsubscribe callback removes the handler", () => {
  const { client } = makeClient();
  let count = 0;
  const off = client.onNotification(() => { count += 1; });

  client._handleLine(JSON.stringify({ jsonrpc: "2.0", method: "x" }));
  assert.equal(count, 1);

  off();
  client._handleLine(JSON.stringify({ jsonrpc: "2.0", method: "x" }));
  assert.equal(count, 1);
});

test("notification fan-out: multiple subscribers all receive the event", () => {
  const { client } = makeClient();
  let a = 0, b = 0;
  client.onNotification(() => { a += 1; });
  client.onNotification(() => { b += 1; });

  client._handleLine(JSON.stringify({ jsonrpc: "2.0", method: "x" }));
  assert.equal(a, 1);
  assert.equal(b, 1);
});

test("emitNotification: lets the polling layer fan out through the same emitter", () => {
  const { client } = makeClient();
  const seen: string[] = [];
  client.onNotification((method) => seen.push(method));

  client.emitNotification("synthetic_event", { foo: 1 });
  assert.equal(seen[0], "synthetic_event");
});

test("call: rejects with a timeout error when no response arrives in time", async () => {
  const { client } = makeClient();
  await assert.rejects(
    () => client.call("never_replies", undefined, 25),
    /MCP call never_replies timed out after 25 ms/,
  );
});

test("call: a late response after timeout does not crash and is dropped", async () => {
  const { client } = makeClient();
  await assert.rejects(
    () => client.call("late", undefined, 10),
    /timed out/,
  );
  assert.doesNotThrow(() =>
    client._handleLine(JSON.stringify({ jsonrpc: "2.0", id: 1, result: { ok: true } })),
  );
});

test("call: clears the timeout when a response arrives in time", async () => {
  const { client } = makeClient();
  const pending = client.call("quick", undefined, 50);
  client._handleLine(JSON.stringify({ jsonrpc: "2.0", id: 1, result: { ok: true } }));
  const result = await pending;
  assert.deepEqual(result, { ok: true });
});

test("dispose: in-flight call rejects with a clear error and follow-up calls reject too", async () => {
  const { client } = makeClient();
  const pending = client.call("inflight", undefined, 5_000);
  client.dispose();
  await assert.rejects(pending, /MCP client disposed/);
  await assert.rejects(client.call("after_dispose"), /already terminated/);
});
