'use strict';
// Cross-platform launcher: picks copilot-harness.exe (Windows) or copilot-harness (Linux/Mac).
const { spawn } = require('child_process');
const path = require('path');

const bin = process.platform === 'win32'
  ? path.join(__dirname, 'copilot-harness.exe')
  : path.join(__dirname, 'copilot-harness');

const child = spawn(bin, process.argv.slice(2), { stdio: 'inherit' });
child.on('exit', (code) => process.exit(code ?? 0));
child.on('error', (err) => {
  process.stderr.write(`CopilotHarness launcher error: ${err.message}\n`);
  process.exit(1);
});
