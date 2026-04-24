/*
 * CopilotHarness Dashboard — sidebar webview app.
 *
 * Consumes postMessage events from the extension and mutates DOM to render
 * the pipeline card + direct-mode card. Runs inside a WebviewView docked in
 * VS Code's auxiliary sidebar.
 */

(function () {
  "use strict";

  const vscode = acquireVsCodeApi();
  const body = document.getElementById("dash-body");
  const emptyState = document.getElementById("empty-state");

  const state = vscode.getState() || { sessions: [] };
  function saveState() { vscode.setState(state); }

  // ── DOM helpers ─────────────────────────────────────────────────────────
  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const k of Object.keys(attrs)) {
        const v = attrs[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "dataset") {
          for (const dk of Object.keys(v)) el.dataset[dk] = v[dk];
        } else if (k === "html") {
          el.innerHTML = v;
        } else if (k.startsWith("on") && typeof v === "function") {
          el.addEventListener(k.slice(2), v);
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
  function scrollToBottom() { body.scrollTop = body.scrollHeight; }
  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function truncate(s, n) {
    s = String(s || "");
    return s.length > n ? s.slice(0, n) + "…" : s;
  }

  // ── Tag rendering ───────────────────────────────────────────────────────
  function tagsFor(tags) {
    if (!tags) return [];
    const out = [];
    if (tags.memory)   out.push(h("span", { class: "tag mem",    title: "Tier 1 memory injected" }, "◆ " + tags.memory));
    if (tags.skill)    out.push(h("span", { class: "tag skill",  title: "Skill pushed by harness" }, "◈ " + tags.skill));
    if (tags.schema)   out.push(h("span", { class: "tag schema", title: "Output schema enforced" }, "{ } " + tags.schema));
    if (tags.firewall) out.push(h("span", { class: "tag fw",     title: "Context firewall" }, "⟡ " + tags.firewall));
    if (tags.policy)   out.push(h("span", { class: "tag policy", title: "Tool policy" }, "◇ " + tags.policy));
    return out;
  }

  // ── Stage index helper ──────────────────────────────────────────────────
  function indexOfStage(stage) {
    return ["plan", "design", "code", "review"].indexOf(stage);
  }
  function agentNameForStage(stage) {
    return { plan: "planner", design: "designer", code: "coder", review: "reviewer" }[stage] || stage;
  }

  // ── Session card ────────────────────────────────────────────────────────
  function ensureSessionCard(msg) {
    let card = body.querySelector(`.pipe-card[data-session-id="${msg.sessionId}"]`);
    if (card) return card;

    hideEmpty();

    const cardEl = h("div", { class: "pipe-card", dataset: { sessionId: msg.sessionId } }, [
      h("div", { class: "pipe-head" }, [
        h("span", { class: "route-pill" }, (msg.route || "/pipeline").replace(/^\//, "").toUpperCase()),
        h("span", { class: "pipe-title", title: msg.request || "" }, truncate(msg.request || msg.pipelineName || "pipeline", 80)),
        h("span", {
          class: "pipe-meta",
          html: `<b>level</b> ${msg.level ?? "?"} · <b>retries</b> <span data-role="retries">0/3</span> · <b>elapsed</b> <span data-role="elapsed">0.0s</span>`,
        }),
      ]),
      h("div", { class: "pipe-body", dataset: { role: "pipe-body" } }),
      h("div", { class: "pipe-foot" }, [
        h("span", {}, ["session ", h("span", { class: "sid" }, msg.sessionId)]),
        h("div", { class: "btns" }, [
          h("button", { class: "btn", dataset: { action: "status" } }, "/status"),
          h("button", { class: "btn", dataset: { action: "cancel" } }, "Cancel"),
          h("button", { class: "btn primary", dataset: { action: "view-plan" } }, "plan.md"),
        ]),
      ]),
    ]);

    body.appendChild(cardEl);
    scrollToBottom();
    return cardEl;
  }

  function stageEl(card, stage, attempt) {
    const pipeBody = card.querySelector('[data-role="pipe-body"]');
    const existing = Array.from(pipeBody.querySelectorAll(`.stage[data-stage="${stage}"]`));
    const byAttempt = existing.find(el => Number(el.dataset.attempt) === Number(attempt));
    if (byAttempt) return byAttempt;

    const el = h("div", {
      class: "stage",
      dataset: { stage, attempt: String(attempt), t0: String(Date.now()) },
    }, [
      h("div", { class: "stage-dot" }, [
        h("span", { dataset: { role: "dot-body" } }, String(indexOfStage(stage) + 1)),
      ]),
      h("div", { class: "stage-main" }, [
        h("div", { class: "stage-row1" }, [
          h("span", { class: "stage-name" }, agentNameForStage(stage)),
          h("span", { dataset: { role: "tags" } }),
          h("span", { class: "collapsed-summary", dataset: { role: "summary" } }),
        ]),
        h("div", { class: "stage-detail", dataset: { role: "detail" } }),
      ]),
      h("div", { class: "stage-side" }, [
        h("span", { class: "time",    dataset: { role: "time" } }, "0.0s"),
        h("br"),
        h("span", { class: "attempt", dataset: { role: "attempt" } }),
      ]),
    ]);
    pipeBody.appendChild(el);
    return el;
  }

  function setStageDot(el, status) {
    el.classList.remove("pass", "fail", "running", "retry");
    if (status) el.classList.add(status);
    const dot = el.querySelector('[data-role="dot-body"]');
    if (!dot) return;
    if (status === "pass")    dot.innerHTML = '<svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 5l2 2 4-4"/></svg>';
    else if (status === "running") dot.innerHTML = '<svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="5" cy="5" r="3"/></svg>';
    else if (status === "fail")  dot.textContent = "✗";
    else if (status === "retry") dot.textContent = "↻";
    else dot.textContent = String(indexOfStage(el.dataset.stage) + 1);
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
    host.innerHTML = html || "";
  }
  function setAttempt(el, attempt, max) {
    const host = el.querySelector('[data-role="attempt"]');
    if (!host) return;
    host.textContent = (attempt && attempt > 0) ? `attempt ${attempt}/${max || 3}` : "";
  }
  function setTime(el, ms) {
    const host = el.querySelector('[data-role="time"]');
    if (host) host.textContent = fmtSeconds(ms);
  }
  function collapse(el, summary) {
    el.classList.add("collapsed");
    const s = el.querySelector('[data-role="summary"]');
    if (s && summary) s.textContent = "— " + summary;
    const d = el.querySelector('[data-role="detail"]');
    if (d) d.innerHTML = "";
  }
  function appendRetryBlock(stageEl, payload) {
    const main = stageEl.querySelector(".stage-main");
    if (!main) return;
    main.appendChild(h("div", { class: "retry-block" }, [
      h("div", {}, [
        h("span", { class: "verdict" }, `reviewer → ${payload.reviewerVerdict || "fail"}`),
        " ",
        h("span", { style: "color:var(--fg-muted)" },
          `· ${payload.issuesCount ?? "—"} issue(s)`),
      ]),
      payload.fixInstructions
        ? h("div", { class: "fix" }, "Fix: " + truncate(payload.fixInstructions, 160))
        : null,
    ]));
  }

  function updateRetries(card, attempt, max) {
    const el = card.querySelector('[data-role="retries"]');
    if (el) el.textContent = `${Math.max(0, attempt - 1)}/${max || 3}`;
  }
  function updateElapsed(card, ms) {
    const el = card.querySelector('[data-role="elapsed"]');
    if (el) el.textContent = fmtSeconds(ms);
  }

  // ── Direct-mode rendering ───────────────────────────────────────────────
  function renderDirectStart(msg) {
    hideEmpty();
    const card = h("div", { class: "direct-card", dataset: { role: "direct-card" } }, [
      h("span", { class: "pill" }, "DIRECT"),
      h("span", {}, "single Copilot call · no pipeline · no evaluator"),
      h("span", { dataset: { role: "skill-pulls" } }),
      msg.prompt ? h("div", { class: "prompt", title: msg.prompt }, truncate(msg.prompt, 200)) : null,
    ]);
    body.appendChild(card);
    scrollToBottom();
  }
  function appendDirectPull(skillId) {
    const cards = body.querySelectorAll('.direct-card[data-role="direct-card"]');
    const card = cards[cards.length - 1];
    if (!card) return;
    const host = card.querySelector('[data-role="skill-pulls"]');
    if (host) host.appendChild(h("span", { class: "pull" }, `↓ ${skillId}`));
  }

  // ── Event handler ───────────────────────────────────────────────────────
  function onMessage(e) {
    const m = e.data;
    if (!m || typeof m !== "object") return;

    try {
      switch (m.type) {
        case "session_start": {
          const card = ensureSessionCard(m);
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
            const existing = foot.querySelector('[data-role="status"]');
            if (existing) existing.remove();
            const statusEl = h("span", {
              class: m.success ? "ok" : "err",
              dataset: { role: "status" },
              style: "margin-left:auto; padding-right:6px; font-weight:600",
            }, m.escalated ? "escalated" : m.success ? "complete ✓" : "failed");
            foot.insertBefore(statusEl, foot.querySelector(".btns"));
          }
          break;
        }
        case "tick": {
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
        case "reset":
          body.innerHTML = "";
          if (emptyState) body.appendChild(emptyState);
          break;
      }
    } catch (err) {
      console.error("dashboard render error", err, m);
    }
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
    else if (action === "view-plan") {
      vscode.postMessage({
        type: "action_view_file",
        relPath: `.harness/sessions/${sessionId}/plan.md`,
      });
    }
  });

  window.addEventListener("message", onMessage);
  vscode.postMessage({ type: "ready" });
})();
