/**
 * Minimal MCP stdio client — newline-delimited JSON-RPC 2.0.
 * Spawns the harness server as a child process and exposes listTools / callTool.
 */

import * as child_process from "child_process";
import { EventEmitter } from "events";
import * as readline from "readline";

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id?: number;
  result?: unknown;
  error?: { code: number; message: string };
}

interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}

export interface McpToolDef {
  name: string;
  description?: string;
}

export type NotificationHandler = (method: string, params: unknown) => void;

interface ProcessLike {
  stdin: NodeJS.WritableStream | null;
  stdout: NodeJS.ReadableStream | null;
  kill(): void;
}

export interface CreateOptions {
  /** Called once per stderr line from the spawned server. */
  onStderr?: (line: string) => void;
  /** Hard cap on the initialize handshake (default 15 000 ms). */
  initializeTimeoutMs?: number;
  /** Default timeout for subsequent calls (default 60 000 ms). */
  defaultCallTimeoutMs?: number;
}

const DEFAULT_INITIALIZE_TIMEOUT_MS = 15_000;
const DEFAULT_CALL_TIMEOUT_MS = 60_000;

export class McpClient {
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void; timer?: NodeJS.Timeout }>();
  private readonly emitter = new EventEmitter();
  private defaultCallTimeoutMs = DEFAULT_CALL_TIMEOUT_MS;
  private disposed = false;

  private constructor(private readonly proc: ProcessLike) {
    const rl = readline.createInterface({ input: proc.stdout! });
    rl.on("line", (line) => this._handleLine(line));
  }

  /** Test-only factory: drive the client from any pair of streams. */
  static _forTest(proc: ProcessLike): McpClient {
    return new McpClient(proc);
  }

  /** Exposed for tests; the readline listener calls this for every stdout line. */
  _handleLine(line: string): void {
    line = line.trim();
    if (!line) { return; }
    let msg: JsonRpcResponse | JsonRpcNotification;
    try { msg = JSON.parse(line); } catch { return; }
    if ((msg as JsonRpcResponse).id === undefined) {
      const note = msg as JsonRpcNotification;
      if (typeof note.method === "string") {
        this.emitter.emit("notification", note.method, note.params);
      }
      return;
    }
    const resp = msg as JsonRpcResponse;
    const p = this.pending.get(resp.id!);
    if (!p) { return; }
    this.pending.delete(resp.id!);
    if (p.timer) { clearTimeout(p.timer); }
    if (resp.error) {
      p.reject(new Error(`MCP ${resp.error.code}: ${resp.error.message}`));
    } else {
      p.resolve(resp.result);
    }
  }

  /** Reject every in-flight call with the same error. Used on process exit. */
  private failAllPending(err: Error): void {
    for (const [, p] of this.pending) {
      if (p.timer) { clearTimeout(p.timer); }
      p.reject(err);
    }
    this.pending.clear();
  }

  onNotification(handler: NotificationHandler): () => void {
    this.emitter.on("notification", handler);
    return () => this.emitter.off("notification", handler);
  }

  emitNotification(method: string, params: unknown): void {
    this.emitter.emit("notification", method, params);
  }

  static async create(
    command: string,
    args: string[],
    env?: Record<string, string>,
    options: CreateOptions = {},
  ): Promise<McpClient> {
    const proc = child_process.spawn(command, args, {
      // stderr piped (not inherited) so Python tracebacks land in the
      // CopilotHarness output channel via options.onStderr instead of
      // disappearing into VS Code's main stderr.
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, ...env },
    });

    const client = new McpClient(proc as ProcessLike);
    client.defaultCallTimeoutMs = options.defaultCallTimeoutMs ?? DEFAULT_CALL_TIMEOUT_MS;

    if (options.onStderr && proc.stderr) {
      const rl = readline.createInterface({ input: proc.stderr });
      rl.on("line", (line) => options.onStderr!(line));
    }

    // If the process dies before it answers initialize (or any later call),
    // resolve every pending promise with a real error rather than letting
    // activate() hang forever.
    proc.on("exit", (code, signal) => {
      client.disposed = true;
      const reason = signal ? `signal ${signal}` : `exit code ${code}`;
      client.failAllPending(new Error(`MCP server terminated (${reason})`));
    });
    proc.on("error", (err) => {
      client.disposed = true;
      client.failAllPending(new Error(`MCP server spawn failed: ${err.message}`));
    });

    await client.call(
      "initialize",
      {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "copilot-harness-extension", version: "0.1.0" },
      },
      options.initializeTimeoutMs ?? DEFAULT_INITIALIZE_TIMEOUT_MS,
    );
    client.notify("notifications/initialized");

    return client;
  }

  private notify(method: string, params?: unknown): void {
    const msg = { jsonrpc: "2.0", method, ...(params !== undefined ? { params } : {}) };
    this.proc.stdin!.write(JSON.stringify(msg) + "\n");
  }

  call(method: string, params?: unknown, timeoutMs?: number): Promise<unknown> {
    return new Promise((resolve, reject) => {
      if (this.disposed) {
        reject(new Error(`MCP server already terminated; call ${method} rejected`));
        return;
      }
      const id = this.nextId++;
      const cap = timeoutMs ?? this.defaultCallTimeoutMs;
      const timer = cap > 0 ? setTimeout(() => {
        if (!this.pending.has(id)) { return; }
        this.pending.delete(id);
        reject(new Error(`MCP call ${method} timed out after ${cap} ms`));
      }, cap) : undefined;
      this.pending.set(id, { resolve, reject, timer });
      const msg = { jsonrpc: "2.0", id, method, ...(params !== undefined ? { params } : {}) };
      this.proc.stdin!.write(JSON.stringify(msg) + "\n");
    });
  }

  async listTools(): Promise<McpToolDef[]> {
    const result = await this.call("tools/list") as { tools: McpToolDef[] };
    return result.tools ?? [];
  }

  async callTool(name: string, args: Record<string, unknown>, timeoutMs?: number): Promise<string> {
    const result = await this.call("tools/call", { name, arguments: args }, timeoutMs) as {
      content: Array<{ type: string; text?: string }>;
    };
    return (result.content ?? []).map((c) => c.text ?? "").join("");
  }

  dispose(): void {
    this.disposed = true;
    this.failAllPending(new Error("MCP client disposed"));
    this.proc.kill();
  }
}
