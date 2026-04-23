/*
 * CopilotHarness Dashboard — webview app.
 *
 * Consumes events posted from the extension host (postMessage) and mutates
 * the DOM to render the pipeline card from the design mockup. No network
 * calls, no dynamic code execution — the CSP denies both anyway.
 */

(function () {
  "use strict";

  const vscode = acquireVsCodeApi();
  const body = document.getElementById("chat-body");
  const emptyState = document.getElementById("empty-state");

  // ── State held in the webview so reloads can restore visuals ────────────
  // Not the source of truth — the extension keeps that. We store enough to
  // redraw if the webview is serialized/deserialized by VS Code.
  const state = vscode.getState() || { sessions: [], direct: [], elapsedMs: 0 };
  function saveState() { vscode.setState(state); }

  // ── Utility helpers ─────────────────────────────────────────────────────
  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const k of Object.keys(attrs)) {
        const v = attrs[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "dataset") {
          for (const dk of Object.keys(v)) el.dataset[dk] = v[dk];
        } else if (k.startsWith("on") && typeof v === "function") {
          el.addEventListener(k.slice(2), v);
        } else if (k === "html") {
          el.innerHTML = v;
        } else {
          el.setAttribute(k, String(v));
        }
      }
    }
    if (children != null) {
      const list = Array.isArray(children) ? children : [children];
      for (const c of list) {
        if (c === null || c === undefined || c === false) continue;
        el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
      }
    }
    return el;
  }
  function fmtSeconds(ms) {
    if (!Number.isFinite(ms) || ms < 0) return "—";
    const s = ms / 1000;
    return s < 10 ? s.toFixed(1) + "s" : Math.round(s) + "s";
  }
  function hideEmpty() {
    if (emptyState && emptyState.parentNode) emptyState.remove();
  }
  function scrollToBottom() {
    body.scrollTop = body.scrollHeight;
  }

  // ── Tag rendering ───────────────────────────────────────────────────────
  function tagsFor(tags) {
    if (!tags) return [];
    const out = [];
    if (tags.memory) out.push(h("span", { class: "tag mem", title: "Tier 1 memory injected" },
      "◆ memory: " + tags.memory));
    if (tags.skill)  out.push(h("span", { class: "tag skill", title: "Skill pushed by harness (cannot opt out)" },
      "◈ skill: " + tags.skill));
    if (tags.schema) out.push(h("span", { class: "tag schema", title: "Output schema enforced" },
      "{ } schema: " + tags.schema));
    if (tags.firewall) out.push(h("span", { class: "tag fw", title: "Context firewall" },
      "⟡ firewall: " + tags.firewall));
    if (tags.policy) out.push(h("span", { class: "tag policy", title: "Tool policy" },
      "◇ policy: " + tags.policy));
    return out;
  }

  // ── Session card construction ───────────────────────────────────────────
  function ensureSessionCard(msg) {
    let card = body.querySelector(`.pipe-card[data-session-id="${msg.sessionId}"]`);
    if (card) return card;

    hideEmpty();

    // 1. User message
    const userMsg = h("div", { class: "msg" }, [
      h("div", { class: "avatar user" }, initials(msg.request || msg.sessionId)),
      h("div", { class: "msg-body" }, [
        h("div", { class: "msg-who" }, [
          "You ", h("span", { class: "sub" }, `· ${nowTime()} · ${msg.route || "pipeline"}`),
        ]),
        h("div", { class: "msg-text", style: "font-size:12.5px; line-height:1.4" }, [
          h("span", { class: "handle" }, "@harness"),
          " ",
          msg.route ? h("span", { style: "color:var(--purple)" }, msg.route) : null,
          msg.route ? " " : null,
          msg.request || "",
        ]),
      ]),
    ]);
    body.appendChild(userMsg);

    // 2. Harness response wrapper + pipe-card
    const cardEl = h("div", { class: "pipe-card", dataset: { sessionId: msg.sessionId } }, [
      h("div", { class: "pipe-head" }, [
        h("span", { class: "route-pill" }, (msg.route || "/pipeline").toUpperCase()),
        h("span", { class: "pipe-title" },
          msg.pipelineName ? `${pipelineLabel(msg)}` : "Governed pipeline"),
        h("span", { class: "pipe-meta", dataset: { role: "meta" }, html:
          `<b>level</b> ${msg.level ?? "?"} · <b>retries</b> <span data-role="retries">0/3</span> · <b>elapsed</b> <span data-role="elapsed">0.0s</span>` }),
      ]),
      h("div", { class: "pipe-body", dataset: { role: "pipe-body" } }),
      h("div", { class: "pipe-foot" }, [
        h("span", {}, ["session ", h("span", { class: "sid" }, msg.sessionId)]),
        h("span", { class: "sp" }, "·"),
        h("span", { dataset: { role: "hooks" } }, ["hooks ", h("span", { class: "ok" }, "PreToolUse ✓ · PostToolUse ✓")]),
        h("span", { class: "sp" }, "·"),
        h("span", { dataset: { role: "audit" } }, ["audit ", h("span", { class: "ok" }, "0 rows")]),
        h("div", { class: "btns" }, [
          h("button", { class: "btn", dataset: { action: "status" } }, "/status"),
          h("button", { class: "btn", dataset: { action: "cancel" } }, "Cancel"),
          h("button", { class: "btn primary", dataset: { action: "view-plan" } }, "View plan.md"),
        ]),
      ]),
    ]);

    const harnessMsg = h("div", { class: "msg" }, [
      h("div", { class: "avatar harness" }, "◇"),
      h("div", { class: "msg-body" }, [
        h("div", { class: "msg-who" }, [
          "CopilotHarness ",
          h("span", { class: "sub" }, `· ${nowTime()} · ${msg.pipelineName || "pipeline"} · level ${msg.level ?? "?"}`),
        ]),
        cardEl,
      ]),
    ]);
    body.appendChild(harnessMsg);
    scrollToBottom();
    return cardEl;
  }
  function pipelineLabel(msg) {
    const agents = msg.agents && msg.agents.length ? msg.agents.length : null;
    return agents ? `${agents}-agent governed pipeline` : "Governed pipeline";
  }

  function initials(s) {
    s = String(s || "").trim();
    if (!s) return "?";
    const words = s.split(/\s+/).slice(0, 2);
    return words.map(w => w[0]).join("").toUpperCase();
  }
  function nowTime() {
    const d = new Date();
    return d.toTimeString().slice(0, 5);
  }

  // ── Stage rendering ─────────────────────────────────────────────────────
  function stageEl(card, stage, attempt) {
    // Attempt > 1 means a retry — we append a new row rather than replacing
    // so the history is preserved (matches mockup).
    const pipeBody = card.querySelector('[data-role="pipe-body"]');
    const existing = Array.from(pipeBody.querySelectorAll(`.stage[data-stage="${stage}"]`));
    const byAttempt = existing.find(el => Number(el.dataset.attempt) === Number(attempt));
    if (byAttempt) return byAttempt;

    const el = h("div", {
      class: "stage",
      dataset: { stage, attempt: String(attempt), t0: String(Date.now()) },
    }, [
      h("div", { class: "stage-dot" }, [h("span", { dataset: { role: "dot-body" } }, String(indexOfStage(stage) + 1))]),
      h("div", { class: "stage-main" }, [
        h("div", { class: "stage-row1" }, [
          h("span", { class: "stage-name" }, agentNameForStage(stage)),
          h("span", { class: "stage-arrow" }, "›"),
          h("span", { dataset: { role: "tags" } }),
          h("span", { class: "collapsed-summary", dataset: { role: "summary" } }),
        ]),
        h("div", { class: "stage-detail", dataset: { role: "detail" } }),
      ]),
      h("div", { class: "stage-side" }, [
        h("span", { class: "time", dataset: { role: "time" } }, "0.0s"),
        h("br"),
        h("span", { class: "attempt", dataset: { role: "attempt" } }),
      ]),
    ]);
    pipeBody.appendChild(el);
    return el;
  }

  function indexOfStage(stage) {
    return ["plan", "design", "code", "review"].indexOf(stage);
  }
  function agentNameForStage(stage) {
    return { plan: "planner", design: "designer", code: "coder", review: "reviewer" }[stage] || stage;
  }

  function setStageDot(el, status, text) {
    el.classList.remove("pass", "fail", "running", "retry");
    if (status) el.classList.add(status);
    const body = el.querySelector('[data-role="dot-body"]');
    if (body) {
      if (status === "pass") body.innerHTML = '<svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 5l2 2 4-4"/></svg>';
      else if (status === "running") body.innerHTML = '<svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="5" cy="5" r="3"/></svg>';
      else if (status === "fail") body.textContent = "✗";
      else if (status === "retry") body.textContent = "↻";
      else body.textContent = text || String(indexOfStage(el.dataset.stage) + 1);
    }
  }

  function setTags(el, tags) {
    const host = el.querySelector('[data-role="tags"]');
    if (!host) return;
    host.innerHTML = "";
    for (const t of tagsFor(tags)) host.appendChild(t);
  }
  function setDetail(el, html) {
    const host = el.querySelector('[data-role="detail"]');
    if (!host) return;
    if (!html) { host.innerHTML = ""; return; }
    host.innerHTML = html;
  }
  function setAttempt(el, attempt, max) {
    const host = el.querySelector('[data-role="attempt"]');
    if (!host) return;
    if (attempt && attempt > 0) host.textContent = `attempt ${attempt}/${max || 3}`;
    else host.textContent = "";
  }
  function setTime(el, ms) {
    const host = el.querySelector('[data-role="time"]');
    if (host) host.textContent = fmtSeconds(ms);
  }
  function collapse(el, summary) {
    el.classList.add("collapsed");
    const s = el.querySelector('[data-role="summary"]');
    if (s && summary) s.textContent = "— " + summary;
    const detail = el.querySelector('[data-role="detail"]');
    if (detail) detail.innerHTML = "";
  }

  function appendRetryBlock(stageEl, payload) {
    const main = stageEl.querySelector(".stage-main");
    if (!main) return;
    const block = h("div", { class: "retry-block" }, [
      h("div", {}, [
        h("span", { class: "verdict" }, `reviewer → ${payload.reviewerVerdict || "fail"}`),
        " ",
        h("span", { style: "color:var(--fg-muted)" },
          `· ${payload.issuesCount ? payload.issuesCount : "—"} issue(s)`),
      ]),
      payload.fixInstructions ? h("div", { class: "fix" },
        "→ fix_instructions: " + truncate(payload.fixInstructions, 180)) : null,
    ]);
    main.appendChild(block);
  }
  function truncate(s, n) {
    s = String(s || "");
    return s.length > n ? s.slice(0, n) + "…" : s;
  }

  // ── Session retries / elapsed counter ───────────────────────────────────
  function updateRetries(card, attempt, max) {
    const el = card.querySelector('[data-role="retries"]');
    if (el) el.textContent = `${Math.max(0, attempt - 1)}/${max || 3}`;
  }
  function updateElapsed(card, ms) {
    const el = card.querySelector('[data-role="elapsed"]');
    if (el) el.textContent = fmtSeconds(ms);
  }
  function updateAudit(card, rows) {
    const host = card.querySelector('[data-role="audit"]');
    if (host) host.innerHTML = `audit <span class="ok">${rows} rows</span>`;
  }
  function updateHooks(card, hook, status) {
    const host = card.querySelector('[data-role="hooks"]');
    if (!host) return;
    const cls = status === "ok" ? "ok" : "err";
    host.innerHTML = `hooks <span class="${cls}">${hook} ${status === "ok" ? "✓" : "✗"}</span>`;
  }

  // ── Direct-mode rendering ───────────────────────────────────────────────
  function renderDirectStart(msg) {
    hideEmpty();
    const userMsg = h("div", { class: "msg" }, [
      h("div", { class: "avatar user" }, "?"),
      h("div", { class: "msg-body" }, [
        h("div", { class: "msg-who" }, ["You ", h("span", { class: "sub" }, `· ${nowTime()} · direct`)]),
        h("div", { class: "msg-text" }, msg.prompt || ""),
      ]),
    ]);
    body.appendChild(userMsg);

    const direct = h("div", { class: "direct-card", dataset: { role: "direct-card" } }, [
      h("span", { class: "pill" }, "DIRECT"),
      h("span", {}, "single Copilot call · no pipeline · no evaluator"),
      h("span", { dataset: { role: "skill-pulls" } }),
    ]);

    const harnessMsg = h("div", { class: "msg" }, [
      h("div", { class: "avatar harness" }, "◇"),
      h("div", { class: "msg-body" }, [
        h("div", { class: "msg-who" }, [
          "CopilotHarness ",
          h("span", { class: "sub" }, `· ${nowTime()} · direct mode`),
        ]),
        direct,
      ]),
    ]);
    body.appendChild(harnessMsg);
    scrollToBottom();
    return direct;
  }
  function appendDirectPull(skillId) {
    const card = body.querySelector('.direct-card[data-role="direct-card"]');
    if (!card) return;
    const host = card.querySelector('[data-role="skill-pulls"]');
    if (!host) return;
    host.appendChild(h("span", { class: "pull" }, `↓ ${skillId}`));
  }
  function finalizeDirect() {
    // No-op for now; the empty state is already hidden and VS Code chat
    // streams the actual answer. We could render a checkmark pill here.
    const card = body.querySelector('.direct-card[data-role="direct-card"]');
    if (card) card.dataset.role = "direct-card-done";
  }

  // ── Message bus ─────────────────────────────────────────────────────────
  function onMessage(e) {
    const m = e.data;
    if (!m || typeof m !== "object") return;

    try {
      switch (m.type) {
        case "session_start": {
          const card = ensureSessionCard(m);
          // Pre-create queued stage rows so the reader can see what's coming.
          if (Array.isArray(m.agents)) {
            for (const a of m.agents) {
              const el = stageEl(card, a.stage, 1);
              setTags(el, a.tags);
            }
          }
          state.sessions.push({ id: m.sessionId, t0: Date.now() });
          saveState();
          break;
        }
        case "stage_start": {
          const card = body.querySelector(`.pipe-card[data-session-id="${m.sessionId}"]`);
          if (!card) break;
          const el = stageEl(card, m.stage, m.attempt || 1);
          el.dataset.t0 = String(Date.now());
          setStageDot(el, "running");
          setTags(el, m.tags);
          setAttempt(el, (m.attempt && m.attempt > 1) ? m.attempt : 0, m.maxAttempts);
          if (m.attempt && m.attempt > 1) updateRetries(card, m.attempt, m.maxAttempts);
          scrollToBottom();
          break;
        }
        case "stage_progress": {
          const card = body.querySelector(`.pipe-card[data-session-id="${m.sessionId}"]`);
          if (!card) break;
          const stages = card.querySelectorAll(`.stage[data-stage="${m.stage}"]`);
          const el = stages[stages.length - 1];
          if (el) setDetail(el, escapeHtml(m.detail || ""));
          break;
        }
        case "stage_complete": {
          const card = body.querySelector(`.pipe-card[data-session-id="${m.sessionId}"]`);
          if (!card) break;
          const stages = card.querySelectorAll(`.stage[data-stage="${m.stage}"]`);
          const el = stages[stages.length - 1];
          if (!el) break;
          setStageDot(el, "pass");
          setTime(el, m.durationMs);
          collapse(el, m.summary || "schema ✓");
          break;
        }
        case "stage_failed": {
          const card = body.querySelector(`.pipe-card[data-session-id="${m.sessionId}"]`);
          if (!card) break;
          const stages = card.querySelectorAll(`.stage[data-stage="${m.stage}"]`);
          const el = stages[stages.length - 1];
          if (!el) break;
          setStageDot(el, "fail");
          setTime(el, m.durationMs);
          if (m.reason) setDetail(el, '<span class="err">' + escapeHtml(m.reason) + "</span>");
          break;
        }
        case "correction_retry": {
          const card = body.querySelector(`.pipe-card[data-session-id="${m.sessionId}"]`);
          if (!card) break;
          // The prior attempt's row transitions from running/pass to retry.
          const stages = card.querySelectorAll(`.stage[data-stage="${m.stage}"]`);
          const prior = stages[stages.length - 1];
          if (prior) {
            setStageDot(prior, "retry");
            appendRetryBlock(prior, m);
            setAttempt(prior, m.attempt - 1, m.maxAttempts);
          }
          updateRetries(card, m.attempt, m.maxAttempts);
          break;
        }
        case "pipeline_complete": {
          const card = body.querySelector(`.pipe-card[data-session-id="${m.sessionId}"]`);
          if (!card) break;
          const foot = card.querySelector(".pipe-foot");
          if (foot) {
            const status = h("span", {
              class: m.success ? "ok" : "err",
              style: "margin-left:auto; padding-right:8px; font-weight:600",
            }, m.escalated ? "escalated" : m.success ? "complete ✓" : "failed");
            foot.insertBefore(status, foot.querySelector(".btns"));
          }
          break;
        }
        case "hook_event": {
          const card = body.querySelector(`.pipe-card[data-session-id="${m.sessionId}"]`);
          if (!card) break;
          updateHooks(card, m.hook, m.status);
          if (typeof m.auditRows === "number") updateAudit(card, m.auditRows);
          break;
        }
        case "tick": {
          // Update elapsed counter on the target card + any running stages.
          const card = m.sessionId
            ? body.querySelector(`.pipe-card[data-session-id="${m.sessionId}"]`)
            : body.querySelector(".pipe-card:last-of-type");
          if (!card) break;
          updateElapsed(card, m.elapsedMs || 0);
          const running = card.querySelectorAll(".stage.running");
          const now = Date.now();
          for (const r of running) {
            const t0 = Number(r.dataset.t0) || now;
            setTime(r, now - t0);
          }
          break;
        }
        case "direct_start":
          renderDirectStart(m);
          break;
        case "direct_pull_skill":
          appendDirectPull(m.skillId);
          break;
        case "direct_complete":
          finalizeDirect();
          break;
        case "reset":
          body.innerHTML = "";
          body.appendChild(emptyState || h("div", { class: "empty-state" },
            "No active session."));
          break;
      }
    } catch (err) {
      // Defensive: never let a broken event kill the panel.
      console.error("dashboard render error", err, m);
    }
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── User actions → extension ────────────────────────────────────────────
  body.addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t instanceof Element)) return;
    const btn = t.closest("button[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    const card = btn.closest(".pipe-card");
    const sessionId = card ? card.dataset.sessionId : null;
    if (action === "status") vscode.postMessage({ type: "action_status" });
    else if (action === "cancel") vscode.postMessage({ type: "action_cancel", sessionId });
    else if (action === "view-plan") vscode.postMessage({ type: "action_view_file", relPath: `.harness/sessions/${sessionId}/plan.md` });
  });

  document.getElementById("suggestions").addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t instanceof Element)) return;
    const sug = t.closest(".sug");
    if (!sug) return;
    const slash = sug.dataset.slash;
    if (slash) vscode.postMessage({ type: "action_run_slash", name: slash });
  });
  document.getElementById("btn-open-chat").addEventListener("click", () =>
    vscode.postMessage({ type: "action_open_chat" }));
  document.getElementById("btn-status").addEventListener("click", () =>
    vscode.postMessage({ type: "action_status" }));

  window.addEventListener("message", onMessage);

  // Tell the extension we're ready to receive events (it may replay state).
  vscode.postMessage({ type: "ready" });
})();
