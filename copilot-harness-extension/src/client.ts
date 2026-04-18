/**
 * HarnessClient — MCP stdio client for the local copilot-harness Python server.
 *
 * Wire format: newline-delimited JSON-RPC 2.0 over the server process's stdin/stdout.
 * Each message is one JSON object per line.
 *
 * Lifecycle:
 *   const client = new HarnessClient(workspaceRoot);
 *   await client.start();           // spawn server, complete MCP handshake
 *   const r = await client.callTool("harness_new_session", { request: "..." });
 *   client.dispose();               // kill server process
 */

import { ChildProcess, spawn } from "child_process";
import * as path from "path";
import * as readline from "readline";

// ── JSON-RPC types ────────────────────────────────────────────────────────────

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: unknown;
}

interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

interface McpContent {
  type: string;
  text: string;
}

interface McpToolResult {
  content: McpContent[];
  isError?: boolean;
}

type PendingHandler = {
  resolve: (r: JsonRpcResponse) => void;
  reject: (e: Error) => void;
};

// ── Client ────────────────────────────────────────────────────────────────────

export class HarnessClient {
  private proc: ChildProcess | null = null;
  private rl: readline.Interface | null = null;
  private readonly pending = new Map<number, PendingHandler>();
  private nextId = 1;
  private ready = false;

  constructor(private readonly workspaceRoot: string) {}

  /** Spawn the MCP server and complete the initialize handshake. */
  async start(): Promise<void> {
    const serverPath = path.join(
      this.workspaceRoot,
      "copilot-harness",
      "server.py",
    );

    this.proc = spawn("python", [serverPath], {
      cwd: this.workspaceRoot,
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.proc.stderr?.on("data", (data: Buffer) => {
      // Forward server stderr to VS Code's developer console for debugging.
      console.error("[harness-mcp]", data.toString().trimEnd());
    });

    this.proc.on("error", (err) => {
      this._rejectAll(err);
    });

    this.proc.on("exit", (code) => {
      const err = new Error(`MCP server exited with code ${code ?? "unknown"}`);
      this._rejectAll(err);
    });

    this.rl = readline.createInterface({ input: this.proc.stdout! });
    this.rl.on("line", (line) => this._handleLine(line));

    await this._handshake();
    this.ready = true;
  }

  private _rejectAll(err: Error): void {
    for (const h of this.pending.values()) h.reject(err);
    this.pending.clear();
  }

  private _handleLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) return;
    let msg: JsonRpcResponse;
    try {
      msg = JSON.parse(trimmed) as JsonRpcResponse;
    } catch {
      // Non-JSON output (e.g. startup log lines) — ignore.
      return;
    }
    // Notifications from server have no id — ignore them.
    if (msg.id == null) return;
    const handler = this.pending.get(msg.id);
    if (handler) {
      this.pending.delete(msg.id);
      handler.resolve(msg);
    }
  }

  private _sendRequest(method: string, params?: unknown): Promise<JsonRpcResponse> {
    return new Promise((resolve, reject) => {
      if (!this.proc?.stdin?.writable) {
        reject(new Error("MCP server is not running"));
        return;
      }
      const id = this.nextId++;
      const req: JsonRpcRequest = { jsonrpc: "2.0", id, method, params };
      this.pending.set(id, { resolve, reject });
      this.proc.stdin.write(JSON.stringify(req) + "\n");
    });
  }

  private _sendNotification(method: string, params?: unknown): void {
    if (!this.proc?.stdin?.writable) return;
    const msg: JsonRpcNotification = { jsonrpc: "2.0", method, params };
    this.proc.stdin.write(JSON.stringify(msg) + "\n");
  }

  private async _handshake(): Promise<void> {
    const resp = await this._sendRequest("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "copilot-harness-extension", version: "0.1.0" },
    });
    if (resp.error) {
      throw new Error(`MCP initialize failed: ${resp.error.message}`);
    }
    // Acknowledge — server does not reply to this notification.
    this._sendNotification("notifications/initialized");
  }

  /**
   * Call a harness MCP tool and return its parsed result.
   *
   * Tool responses whose text is valid JSON are returned as objects.
   * Plain-text responses (e.g. SKILL.md content) are returned as strings.
   */
  async callTool(
    name: string,
    args: Record<string, unknown> = {},
  ): Promise<unknown> {
    if (!this.ready) {
      throw new Error("HarnessClient not started — call start() first");
    }
    const resp = await this._sendRequest("tools/call", {
      name,
      arguments: args,
    });
    if (resp.error) {
      throw new Error(
        `MCP tool error [${resp.error.code}]: ${resp.error.message}`,
      );
    }
    const result = resp.result as McpToolResult;
    if (result.isError) {
      throw new Error(
        `Harness tool '${name}' returned error: ${result.content.map((c) => c.text).join("\n")}`,
      );
    }
    const text = result.content.map((c) => c.text).join("");
    try {
      return JSON.parse(text) as unknown;
    } catch {
      // SKILL.md content and reference files are plain text — return as-is.
      return text;
    }
  }

  /** Stop the server process and clean up. */
  dispose(): void {
    this.rl?.close();
    this.proc?.stdin?.end();
    this.proc?.kill("SIGTERM");
    this.proc = null;
    this.ready = false;
  }
}
