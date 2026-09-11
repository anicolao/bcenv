import { execFileSync, spawnSync } from 'node:child_process';

function git(...args) {
  return execFileSync('git', args, { stdio: ['ignore', 'pipe', 'pipe'] });
}

function reject(message) {
  throw new Error(`${message}\nAppend the exact user prompt to PROMPTS.md and stage it with git add PROMPTS.md. Existing bytes must remain unchanged.`);
}

try {
  const entry = git('ls-files', '--stage', '--', 'PROMPTS.md').toString();
  if (!/^100644 [0-9a-f]+ 0\tPROMPTS\.md\n$/.test(entry)) {
    reject('PROMPTS.md must be staged as a regular, non-executable file.');
  }
  const staged = git('show', ':PROMPTS.md');
  let previous = Buffer.alloc(0);
  // An unborn branch has no HEAD. Other Git errors must still fail the check.
  const head = spawnSync('git', ['rev-parse', '--verify', '--quiet', 'HEAD']);
  if (head.error) throw head.error;
  if (head.status !== 0 && head.status !== 1) throw new Error('Cannot resolve Git HEAD.');
  if (head.status === 0) {
    const tracked = git('ls-tree', 'HEAD', '--', 'PROMPTS.md');
    if (tracked.length) previous = git('show', 'HEAD:PROMPTS.md');
  }
  if (!staged.subarray(0, previous.length).equals(previous)) {
    reject('PROMPTS.md is append-only: existing content was changed or removed.');
  }
  if (!staged.subarray(previous.length).toString('utf8').trim()) {
    reject('Every commit must add non-whitespace content to PROMPTS.md.');
  }
} catch (error) {
  console.error(`Prompt record check failed: ${error.message}`);
  process.exitCode = 1;
}
