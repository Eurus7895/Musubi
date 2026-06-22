#!/usr/bin/env node
/* eslint-disable no-undef */
/**
 * run-bash.js — invoke a bash script through Git Bash on Windows, /bin/bash
 * elsewhere. Used by every npm script that wraps a .sh file.
 *
 * Why not just `bash scripts/x.sh` in package.json? On Windows, npm spawns
 * the script via cmd.exe and `bash` resolves to whatever is first on PATH.
 * If the user has WSL installed, `C:\Windows\System32\bash.exe` (the WSL
 * launcher) wins ahead of Git Bash. That ships the script into a Linux
 * distro that has no access to the user's Windows Python or VS Code, and
 * silently breaks setup, packaging, and install. This wrapper hardcodes
 * the Git Bash candidate paths so the choice is independent of PATH order.
 *
 * Usage:  node scripts/run-bash.js scripts/<name>.sh [args...]
 */

const cp = require("child_process");
const fs = require("fs");
const path = require("path");

const script = process.argv[2];
if (!script) {
    console.error("Usage: run-bash.js <script.sh> [args...]");
    process.exit(2);
}
const extraArgs = process.argv.slice(3);

function findBash() {
    if (process.platform !== "win32") {
        return "/bin/bash";
    }
    // Standard Git for Windows install locations, in priority order. The
    // 64-bit installer drops Git into PROGRAMFILES; the 32-bit installer
    // (or the per-user installer) lands in LOCALAPPDATA / PROGRAMFILES(X86).
    const candidates = [
        process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, "Git", "bin", "bash.exe"),
        process.env["PROGRAMFILES(X86)"] && path.join(process.env["PROGRAMFILES(X86)"], "Git", "bin", "bash.exe"),
        process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Programs", "Git", "bin", "bash.exe"),
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
    ].filter(Boolean);
    for (const c of candidates) {
        if (fs.existsSync(c)) { return c; }
    }
    return null;
}

const bash = findBash();
if (!bash) {
    console.error("ERROR: Git Bash not found.");
    console.error("  Install Git for Windows: https://git-scm.com/download/win");
    console.error("  After install, the bundled bash lives at C:\\Program Files\\Git\\bin\\bash.exe.");
    process.exit(1);
}

const result = cp.spawnSync(bash, [script, ...extraArgs], { stdio: "inherit" });
if (result.error) {
    console.error(`ERROR running ${script}: ${result.error.message}`);
    process.exit(1);
}
process.exit(result.status ?? 0);
