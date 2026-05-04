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

export class McpClient {
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  private readonly emitter = new EventEmitter();

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
    if (resp.error) {
      p.reject(new Error(`MCP ${resp.error.code}: ${resp.error.message}`));
    } else {
      p.resolve(resp.result);
    }
  }

  onNotification(handler: NotificationHandler): () => void {
    this.emitter.on("notification", handler);
    return () => this.emitter.off("notification", handler);
  }

  emitNotification(method: string, params: unknown): void {
    this.emitter.emit("notification", method, params);
  }

  static async create(command: string, args: string[], env?: Record<string, string>): Promise<McpClient> {
    const proc = child_process.spawn(command, args, {
      stdio: ["pipe", "pipe", "inherit"],
      env: { ...process.env, ...env },
    });

    const client = new McpClient(proc as ProcessLike);

    await client.call("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "copilot-harness-extension", version: "0.1.0" },
    });
    client.notify("notifications/initialized");

    return client;
  }

  private notify(method: string, params?: unknown): void {
    const msg = { jsonrpc: "2.0", method, ...(params !== undefined ? { params } : {}) };
    this.proc.stdin!.write(JSON.stringify(msg) + "\n");
  }

  call(method: string, params?: unknown): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      this.pending.set(id, { resolve, reject });
      const msg = { jsonrpc: "2.0", id, method, ...(params !== undefined ? { params } : {}) };
      this.proc.stdin!.write(JSON.stringify(msg) + "\n");
    });
  }

  async listTools(): Promise<McpToolDef[]> {
    const result = await this.call("tools/list") as { tools: McpToolDef[] };
    return result.tools ?? [];
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<string> {
    const result = await this.call("tools/call", { name, arguments: args }) as {
      content: Array<{ type: string; text?: string }>;
    };
    return (result.content ?? []).map((c) => c.text ?? "").join("");
  }

  dispose(): void {
    this.proc.kill();
  }
}
