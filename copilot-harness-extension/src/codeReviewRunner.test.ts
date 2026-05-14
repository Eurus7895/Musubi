import { test } from "node:test";
import assert from "node:assert/strict";

import { extractFileDiff, resolveCodeReviewInput } from "./codeReviewInput";

// ── extractFileDiff ──────────────────────────────────────────────────────

const SAMPLE_GIT_DIFF = `diff --git a/foo.py b/foo.py
index abc..def 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 def existing():
-    return 1
+    return 2
+    # comment
diff --git a/bar.py b/bar.py
new file mode 100644
index 000..345
--- /dev/null
+++ b/bar.py
@@ -0,0 +1,2 @@
+def new_fn():
+    pass
diff --git a/qux/baz.ts b/qux/baz.ts
index 111..222 100644
--- a/qux/baz.ts
+++ b/qux/baz.ts
@@ -10,3 +10,4 @@ export function helper() {
   return true;
 }
+// new line
`;

test("extractFileDiff: returns the section for the named file", () => {
  const slice = extractFileDiff(SAMPLE_GIT_DIFF, "foo.py");
  assert.ok(slice.includes("--- a/foo.py"));
  assert.ok(slice.includes("+++ b/foo.py"));
  assert.ok(slice.includes("    return 2"));
  assert.ok(!slice.includes("bar.py"));
  assert.ok(!slice.includes("qux/baz.ts"));
});

test("extractFileDiff: extracts a new file section", () => {
  const slice = extractFileDiff(SAMPLE_GIT_DIFF, "bar.py");
  assert.ok(slice.includes("new file mode"));
  assert.ok(slice.includes("+def new_fn():"));
  assert.ok(!slice.includes("foo.py"));
});

test("extractFileDiff: handles nested paths", () => {
  const slice = extractFileDiff(SAMPLE_GIT_DIFF, "qux/baz.ts");
  assert.ok(slice.includes("qux/baz.ts"));
  assert.ok(slice.includes("// new line"));
});

test("extractFileDiff: returns empty string for an unknown path", () => {
  assert.equal(extractFileDiff(SAMPLE_GIT_DIFF, "missing.py"), "");
});

test("extractFileDiff: returns empty string for empty input", () => {
  assert.equal(extractFileDiff("", "foo.py"), "");
});

test("extractFileDiff: handles plain unified diff (no git header)", () => {
  const plain = `--- a/alpha.txt
+++ b/alpha.txt
@@ -1 +1 @@
-old
+new
--- a/beta.txt
+++ b/beta.txt
@@ -1 +1 @@
-x
+y
`;
  const slice = extractFileDiff(plain, "alpha.txt");
  assert.ok(slice.includes("--- a/alpha.txt"));
  // Without `diff --git` headers the section terminates at the next `--- `,
  // but the plain-format branch matches the first section's `--- a/PATH`.
  assert.ok(slice.includes("-old"));
});

// ── resolveCodeReviewInput ───────────────────────────────────────────────

test("resolveCodeReviewInput: empty input is a typed error", () => {
  const r = resolveCodeReviewInput("", "/tmp");
  assert.ok("error" in r);
  if ("error" in r) {
    assert.match(r.error, /No branch specified/);
    assert.match(r.hint ?? "", /Usage/);
  }
});

test("resolveCodeReviewInput: whitespace-only input is a typed error", () => {
  const r = resolveCodeReviewInput("   ", "/tmp");
  assert.ok("error" in r);
});

test("resolveCodeReviewInput: natural-language input is recognised, not git-errored", () => {
  // Regression: first real /code-review run hit "review this project" and
  // got an opaque git error. Now the resolver detects whitespace in the
  // input and returns a usage hint instead.
  const r = resolveCodeReviewInput("review this project", "/tmp");
  assert.ok("error" in r);
  if ("error" in r) {
    assert.match(r.error, /branch name, not a description/);
    assert.match(r.hint ?? "", /Usage:/);
    // The actual input is echoed so the user can see what was parsed.
    assert.ok(r.error.includes("review this project"));
  }
});

test("resolveCodeReviewInput: PR-number input returns a clear hint", () => {
  const r = resolveCodeReviewInput("#42", "/tmp");
  assert.ok("error" in r);
  if ("error" in r) {
    assert.match(r.error, /PR-number/);
    assert.match(r.hint ?? "", /branch/i);
  }
});

test("resolveCodeReviewInput: unknown branch in invalid repo is a typed error", () => {
  // /tmp is not a git repo, so every git diff candidate fails.
  const r = resolveCodeReviewInput("does-not-exist", "/tmp");
  assert.ok("error" in r);
  if ("error" in r) {
    assert.match(r.error, /not found/);
    assert.match(r.hint ?? "", /branch exists/);
  }
});
