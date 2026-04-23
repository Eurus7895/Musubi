/**
 * Minimal MCP stdio client — newline-delimited JSON-RPC 2.0.
 * Spawns the harness server as a child process and exposes listTools / callTool.
 */

import * as child_process from "child_process";
import * as readline from "readline";

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id?: number;
  result?: unknown;
  error?: { code: number; message: string };
}

export interface McpToolDef {
  name: string;
  description?: string;
}

export class McpClient {
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();

  private constructor(private readonly proc: child_process.ChildProcess) {
    const rl = readline.createInterface({ input: proc.stdout! });
    rl.on("line", (line) => {
      line = line.trim();
      if (!line) { return; }
      let msg: JsonRpcResponse;
      try { msg = JSON.parse(line); } catch { return; }
      if (msg.id === undefined) { return; } // notification — ignore
      const p = this.pending.get(msg.id);
      if (!p) { return; }
      this.pending.delete(msg.id);
      if (msg.error) {
        p.reject(new Error(`MCP ${msg.error.code}: ${msg.error.message}`));
      } else {
        p.resolve(msg.result);
      }
    });
  }

  static async create(command: string, args: string[], env?: Record<string, string>): Promise<McpClient> {
    const proc = child_process.spawn(command, args, {
      stdio: ["pipe", "pipe", "inherit"],
      env: { ...process.env, ...env },
    });

    const client = new McpClient(proc);

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
