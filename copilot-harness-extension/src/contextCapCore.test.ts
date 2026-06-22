import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import { resolvePipelineContextCap } from "./contextCapCore";

function makeRoot(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ctxcap-"));
}

function writePipelineYaml(root: string, name: string, body: string): void {
  const dir = path.join(root, ".github", "pipelines", name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "pipeline.yaml"), body, "utf-8");
}

test("resolvePipelineContextCap: returns null when file is missing", () => {
  const root = makeRoot();
  assert.equal(resolvePipelineContextCap([root], "feature-dev"), null);
});

test("resolvePipelineContextCap: returns null when context_cap field is absent", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "name: feature-dev\nlevel: 2\n");
  assert.equal(resolvePipelineContextCap([root], "feature-dev"), null);
});

test("resolvePipelineContextCap: returns the integer value when present", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "name: feature-dev\ncontext_cap: 80000\nlevel: 2\n");
  assert.equal(resolvePipelineContextCap([root], "feature-dev"), 80000);
});

test("resolvePipelineContextCap: tolerates trailing comment", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "name: x\ncontext_cap: 30000   # multi-file pipeline\n");
  assert.equal(resolvePipelineContextCap([root], "feature-dev"), 30000);
});

test("resolvePipelineContextCap: rejects non-integer values", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "name: x\ncontext_cap: 50.5\n");
  assert.equal(resolvePipelineContextCap([root], "feature-dev"), null);
});

test("resolvePipelineContextCap: rejects zero", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "name: x\ncontext_cap: 0\n");
  assert.equal(resolvePipelineContextCap([root], "feature-dev"), null);
});

test("resolvePipelineContextCap: ignores nested context_cap in non-top-level position", () => {
  // A `context_cap:` indented under an agents[] entry would NOT match the
  // anchored regex (regex starts at line-start with no leading whitespace).
  const root = makeRoot();
  writePipelineYaml(
    root,
    "feature-dev",
    "name: x\nagents:\n  - name: planner\n    context_cap: 99999\n",
  );
  assert.equal(resolvePipelineContextCap([root], "feature-dev"), null);
});

test("resolvePipelineContextCap: first root with the file wins", () => {
  const r1 = makeRoot();
  const r2 = makeRoot();
  writePipelineYaml(r1, "feature-dev", "context_cap: 11111\n");
  writePipelineYaml(r2, "feature-dev", "context_cap: 22222\n");
  assert.equal(resolvePipelineContextCap([r1, r2], "feature-dev"), 11111);
  assert.equal(resolvePipelineContextCap([r2, r1], "feature-dev"), 22222);
});

test("resolvePipelineContextCap: rejects pipeline names with path-traversal characters", () => {
  const root = makeRoot();
  writePipelineYaml(root, "feature-dev", "context_cap: 1234\n");
  assert.equal(resolvePipelineContextCap([root], "../feature-dev"), null);
  assert.equal(resolvePipelineContextCap([root], "feature/../dev"), null);
});

test("resolvePipelineContextCap: empty pipeline name returns null", () => {
  const root = makeRoot();
  assert.equal(resolvePipelineContextCap([root], ""), null);
});

test("resolvePipelineContextCap: empty roots array returns null", () => {
  assert.equal(resolvePipelineContextCap([], "feature-dev"), null);
});
