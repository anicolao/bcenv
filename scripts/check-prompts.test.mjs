import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));

test('Husky requires a staged, strictly append-only prompt record', () => {
  const cwd = mkdtempSync(join(tmpdir(), 'bcenv-prompts-'));
  const env = { ...process.env, HUSKY: '1', HUSKY_TEST: '1' };
  // Do not inherit the parent repository's index or user hook configuration.
  for (const key of Object.keys(env)) if (key.startsWith('GIT_')) delete env[key];
  env.XDG_CONFIG_HOME = join(cwd, 'config');
  const git = (...args) => execFileSync('git', args, { cwd, env, stdio: 'pipe' });
  const write = (text) => writeFileSync(join(cwd, 'PROMPTS.md'), text);
  const commit = (accepted, label, ...options) => {
    const result = spawnSync('git', ['commit', '-m', label, ...options], { cwd, env, encoding: 'utf8' });
    assert.equal(result.status === 0, accepted, `${label}: ${result.stdout}${result.stderr}`);
    if (!accepted) assert.match(result.stdout + result.stderr, /Prompt record check failed/);
  };
  try {
    git('init', '-q');
    git('config', 'user.name', 'Hook Test');
    git('config', 'user.email', 'hook-test@example.invalid');
    git('config', 'commit.gpgsign', 'false');
    mkdirSync(join(cwd, 'scripts'));
    mkdirSync(join(cwd, '.husky'));
    for (const path of ['scripts/check-prompts.mjs', '.husky/pre-commit', '.husky/pre-merge-commit']) {
      copyFileSync(join(root, path), join(cwd, path));
    }
    execFileSync(process.execPath, [join(root, 'node_modules/husky/bin.js')], { cwd, env });
    git('add', 'scripts', '.husky');
    commit(false, 'missing initial record');
    write(' \n');
    git('add', 'PROMPTS.md');
    commit(false, 'blank initial record');
    const initial = '# Prompts\n\nExact original prompt.\n';
    write(initial);
    git('add', 'PROMPTS.md');
    commit(true, 'initial record');
    commit(false, 'unchanged record', '--allow-empty');
    write(initial + 'Unstaged prompt.\n');
    commit(false, 'unstaged addition', '--allow-empty');
    write(initial + ' \n\t');
    git('add', 'PROMPTS.md');
    commit(false, 'whitespace-only addition');
    write(initial.replace('original', 'edited') + 'New prompt.\n');
    git('add', 'PROMPTS.md');
    commit(false, 'rewritten history with addition');
    write('# Prompts\n');
    git('add', 'PROMPTS.md');
    commit(false, 'truncated record');
    git('rm', '-f', 'PROMPTS.md');
    commit(false, 'deleted record');
    write(initial + 'Second exact prompt.\n');
    git('add', 'PROMPTS.md');
    // The index is authoritative even if the worktree differs.
    write('Unstaged replacement must not affect the check.\n');
    commit(true, 'valid staged append');
    assert.equal(git('show', 'HEAD:PROMPTS.md').toString(), initial + 'Second exact prompt.\n');
    const mergeCheck = spawnSync('sh', ['.husky/_/pre-merge-commit'], { cwd, env, encoding: 'utf8' });
    assert.notEqual(mergeCheck.status, 0);
    assert.match(mergeCheck.stdout + mergeCheck.stderr, /Prompt record check failed/);
  } finally {
    rmSync(cwd, { recursive: true, force: true });
  }
});
