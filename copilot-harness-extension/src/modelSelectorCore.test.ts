import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import {
  parseAgentModelFamily,
  parseFrontmatterModel,
  pickSkillModelFamily,
  readAgentModelFamily,
  readSkillModelFamily,
} from "./modelSelectorCore";

// ── parseAgentModelFamily ────────────────────────────────────────────

test("parseAgentModelFamily: returns the family declared in frontmatter", () => {
  const md = "---\nname: Foo\nmodel: gpt-4o\nversion: 1.0.0\n---\n\nbody";
  assert.equal(parseAgentModelFamily(md), "gpt-4o");
});

test("parseAgentModelFamily: handles model on the first frontmatter line", () => {
  const md = "---\nmodel: gpt-4o-mini\n---\nbody";
  assert.equal(parseAgentModelFamily(md), "gpt-4o-mini");
});

test("parseAgentModelFamily: returns null when no frontmatter", () => {
  assert.equal(parseAgentModelFamily("# heading\n\nbody"), null);
});

test("parseAgentModelFamily: returns null when frontmatter is unterminated", () => {
  assert.equal(parseAgentModelFamily("---\nmodel: gpt-4o\nbody never closes"), null);
});

test("parseAgentModelFamily: returns null when no model line in frontmatter", () => {
  const md = "---\nname: Foo\nversion: 1.0.0\n---\n\nbody";
  assert.equal(parseAgentModelFamily(md), null);
});

test("parseAgentModelFamily: strips quotes around the value", () => {
  assert.equal(
    parseAgentModelFamily("---\nmodel: 'claude-sonnet-4.5'\n---\n"),
    "claude-sonnet-4.5",
  );
  assert.equal(
    parseAgentModelFamily("---\nmodel: \"gpt-4.1\"\n---\n"),
    "gpt-4.1",
  );
});

test("parseAgentModelFamily: ignores trailing inline comments", () => {
  const md = "---\nmodel: gpt-4o  # heavyweight default\n---\n";
  assert.equal(parseAgentModelFamily(md), "gpt-4o");
});

test("parseAgentModelFamily: tolerates extra leading whitespace", () => {
  const md = "---\n   model: gpt-4o\n---\n";
  assert.equal(parseAgentModelFamily(md), "gpt-4o");
});

test("parseAgentModelFamily: does not match `model` substring in another key", () => {
  const md = "---\nmodels_used: 5\nlabel: model-foo\n---\n";
  assert.equal(parseAgentModelFamily(md), null);
});

// ── readAgentModelFamily ─────────────────────────────────────────────

function withTmpRoots(setup: (root: string) => void): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "msel-"));
  setup(root);
  return root;
}

test("readAgentModelFamily: reads from the first root that has the file", () => {
  const root = withTmpRoots(r => {
    fs.mkdirSync(path.join(r, ".github", "agents"), { recursive: true });
    fs.writeFileSync(
      path.join(r, ".github", "agents", "planner.agent.md"),
      "---\nmodel: gpt-4o\n---\nbody",
      "utf-8",
    );
  });
  try {
    assert.equal(readAgentModelFamily([root], "planner"), "gpt-4o");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readAgentModelFamily: workspace root wins over fallback when both exist", () => {
  const work = withTmpRoots(r => {
    fs.mkdirSync(path.join(r, ".github", "agents"), { recursive: true });
    fs.writeFileSync(
      path.join(r, ".github", "agents", "planner.agent.md"),
      "---\nmodel: claude-sonnet-4.5\n---\n",
      "utf-8",
    );
  });
  const bundle = withTmpRoots(r => {
    fs.mkdirSync(path.join(r, ".github", "agents"), { recursive: true });
    fs.writeFileSync(
      path.join(r, ".github", "agents", "planner.agent.md"),
      "---\nmodel: gpt-4o\n---\n",
      "utf-8",
    );
  });
  try {
    assert.equal(readAgentModelFamily([work, bundle], "planner"), "claude-sonnet-4.5");
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
    fs.rmSync(bundle, { recursive: true, force: true });
  }
});

test("readAgentModelFamily: falls through to fallback root when workspace lacks the file", () => {
  const work = withTmpRoots(() => { /* no agent file in workspace */ });
  const bundle = withTmpRoots(r => {
    fs.mkdirSync(path.join(r, ".github", "agents"), { recursive: true });
    fs.writeFileSync(
      path.join(r, ".github", "agents", "planner.agent.md"),
      "---\nmodel: gpt-4o\n---\n",
      "utf-8",
    );
  });
  try {
    assert.equal(readAgentModelFamily([work, bundle], "planner"), "gpt-4o");
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
    fs.rmSync(bundle, { recursive: true, force: true });
  }
});

test("readAgentModelFamily: returns null when no root has the file", () => {
  const root = withTmpRoots(() => { /* empty */ });
  try {
    assert.equal(readAgentModelFamily([root], "ghost-agent"), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readAgentModelFamily: returns null when the file lacks a `model:` line", () => {
  const root = withTmpRoots(r => {
    fs.mkdirSync(path.join(r, ".github", "agents"), { recursive: true });
    fs.writeFileSync(
      path.join(r, ".github", "agents", "planner.agent.md"),
      "---\nname: Planner\n---\n",
      "utf-8",
    );
  });
  try {
    assert.equal(readAgentModelFamily([root], "planner"), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readAgentModelFamily: skips empty / falsy roots", () => {
  const root = withTmpRoots(r => {
    fs.mkdirSync(path.join(r, ".github", "agents"), { recursive: true });
    fs.writeFileSync(
      path.join(r, ".github", "agents", "coder.agent.md"),
      "---\nmodel: gpt-4o\n---\n",
      "utf-8",
    );
  });
  try {
    assert.equal(readAgentModelFamily(["", root], "coder"), "gpt-4o");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

// ── parseFrontmatterModel alias ──────────────────────────────────────

test("parseFrontmatterModel and parseAgentModelFamily are the same function", () => {
  assert.equal(parseFrontmatterModel, parseAgentModelFamily);
  assert.equal(
    parseFrontmatterModel("---\nmodel: gpt-4.1\n---\n"),
    "gpt-4.1",
  );
});

// ── readSkillModelFamily ─────────────────────────────────────────────

test("readSkillModelFamily: reads model from .github/skills/<id>/SKILL.md", () => {
  const root = withTmpRoots(r => {
    fs.mkdirSync(path.join(r, ".github", "skills", "complex-reasoning"), { recursive: true });
    fs.writeFileSync(
      path.join(r, ".github", "skills", "complex-reasoning", "SKILL.md"),
      "---\nname: complex-reasoning\nmodel: claude-opus-4\n---\nbody",
      "utf-8",
    );
  });
  try {
    assert.equal(readSkillModelFamily([root], "complex-reasoning"), "claude-opus-4");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readSkillModelFamily: returns null when SKILL.md doesn't declare model", () => {
  const root = withTmpRoots(r => {
    fs.mkdirSync(path.join(r, ".github", "skills", "plain"), { recursive: true });
    fs.writeFileSync(
      path.join(r, ".github", "skills", "plain", "SKILL.md"),
      "---\nname: plain\n---\n",
      "utf-8",
    );
  });
  try {
    assert.equal(readSkillModelFamily([root], "plain"), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("readSkillModelFamily: returns null when skill folder doesn't exist", () => {
  const root = withTmpRoots(() => { /* empty */ });
  try {
    assert.equal(readSkillModelFamily([root], "ghost"), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

// ── pickSkillModelFamily ─────────────────────────────────────────────

test("pickSkillModelFamily: returns the first skill that declares a model", () => {
  const root = withTmpRoots(r => {
    const mk = (id: string, body: string) => {
      fs.mkdirSync(path.join(r, ".github", "skills", id), { recursive: true });
      fs.writeFileSync(
        path.join(r, ".github", "skills", id, "SKILL.md"),
        body, "utf-8",
      );
    };
    mk("plain-a", "---\nname: plain-a\n---\n");
    mk("heavy-b", "---\nname: heavy-b\nmodel: claude-opus-4\n---\n");
    mk("heavy-c", "---\nname: heavy-c\nmodel: claude-sonnet-4.5\n---\n");
  });
  try {
    const pick = pickSkillModelFamily([root], ["plain-a", "heavy-b", "heavy-c"]);
    assert.deepEqual(pick, { skillId: "heavy-b", family: "claude-opus-4" });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("pickSkillModelFamily: returns null when no skill declares a model", () => {
  const root = withTmpRoots(r => {
    fs.mkdirSync(path.join(r, ".github", "skills", "plain"), { recursive: true });
    fs.writeFileSync(
      path.join(r, ".github", "skills", "plain", "SKILL.md"),
      "---\nname: plain\n---\n", "utf-8",
    );
  });
  try {
    assert.equal(pickSkillModelFamily([root], ["plain", "absent"]), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("pickSkillModelFamily: returns null on empty skills list", () => {
  assert.equal(pickSkillModelFamily(["/anywhere"], []), null);
});

// Regression: every agent file shipped in this repo declares a model so
// the runtime never silently falls back to the hardcoded default.
test("repo agents: every shipped .agent.md declares a model", () => {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const agentsDir = path.join(repoRoot, ".github", "agents");
  if (!fs.existsSync(agentsDir)) { return; }  // running from a non-repo checkout
  const files = fs.readdirSync(agentsDir).filter(f => f.endsWith(".agent.md"));
  assert.ok(files.length > 0, "expected at least one agent file in .github/agents/");
  const missing: string[] = [];
  for (const f of files) {
    const md = fs.readFileSync(path.join(agentsDir, f), "utf-8");
    if (parseAgentModelFamily(md) === null) { missing.push(f); }
  }
  assert.deepEqual(missing, [], `agents missing 'model:' frontmatter: ${missing.join(", ")}`);
});
