import * as assert from "assert";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { test } from "node:test";

import { readAgentPrompt, resolveAgentPromptPath } from "./agentPromptResolver";

function withTmpRoot(setup: (root: string) => void): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aprompt-"));
  setup(root);
  return root;
}

function write(root: string, rel: string, text: string): void {
  const file = path.join(root, rel);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text, "utf-8");
}

test("resolveAgentPromptPath: worker prompt wins over legacy flat prompt", () => {
  const root = withTmpRoot(r => {
    write(r, ".github/agents/coder.agent.md", "legacy");
    write(r, ".github/agents/workers/coder.agent.md", "worker");
  });
  try {
    assert.equal(
      resolveAgentPromptPath([root], "coder", { purpose: "worker" }),
      path.join(root, ".github", "agents", "workers", "coder.agent.md"),
    );
    assert.equal(readAgentPrompt([root], "coder", { purpose: "worker" }), "worker");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("resolveAgentPromptPath: pipeline-stage prompt wins over legacy variants", () => {
  const root = withTmpRoot(r => {
    write(r, ".github/agents/coder.agent.md", "legacy");
    write(r, ".github/agents/feature-dev-coder.agent.md", "prefixed");
    write(r, ".github/agents/pipeline-stages/feature-dev/coder.agent.md", "stage");
  });
  try {
    assert.equal(
      resolveAgentPromptPath(
        [root],
        "coder",
        { purpose: "pipeline-stage", pipelineName: "feature-dev" },
      ),
      path.join(root, ".github", "agents", "pipeline-stages", "feature-dev", "coder.agent.md"),
    );
    assert.equal(
      readAgentPrompt([root], "coder", { purpose: "pipeline-stage", pipelineName: "feature-dev" }),
      "stage",
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("resolveAgentPromptPath: pipeline-stage falls back to prefixed legacy prompt", () => {
  const root = withTmpRoot(r => {
    write(r, ".github/agents/code-review-finder.agent.md", "prefixed");
    write(r, ".github/agents/finder.agent.md", "legacy");
  });
  try {
    assert.equal(
      resolveAgentPromptPath(
        [root],
        "finder",
        { purpose: "pipeline-stage", pipelineName: "code-review" },
      ),
      path.join(root, ".github", "agents", "code-review-finder.agent.md"),
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("resolveAgentPromptPath: invalid names cannot escape the catalog", () => {
  const root = withTmpRoot(r => {
    write(r, ".github/agents/workers/coder.agent.md", "worker");
  });
  try {
    assert.equal(resolveAgentPromptPath([root], "../coder", { purpose: "worker" }), null);
    assert.equal(
      resolveAgentPromptPath(
        [root],
        "coder",
        { purpose: "pipeline-stage", pipelineName: "../feature-dev" },
      ),
      null,
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
