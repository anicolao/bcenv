#!/usr/bin/env python3
"""Builder-only export of a verified release into a credential-free local pilot."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--store-root', required=True)
p.add_argument('--closure', required=True)
p.add_argument('--bcenv', required=True)
p.add_argument('--engine', required=True)
p.add_argument('--python', required=True)
p.add_argument('--jdk', required=True)
p.add_argument('--output', required=True)
a = p.parse_args()
source = Path(__file__).resolve().parents[1]
store_root = Path(a.store_root).resolve()
out = Path(a.output).resolve()
out.mkdir(parents=True, exist_ok=False)
root = out / 'root'
(root / 'nix/store').mkdir(parents=True)
(root / 'tmp').mkdir()
(root / 'dev').mkdir()
(root / 'dev/null').touch()
workspace = root / 'workspace'
(workspace / 'competitor/src').mkdir(parents=True)
(workspace / 'work').mkdir()
for entry in Path(a.closure).read_text().splitlines():
    path = Path(entry)
    if not entry.startswith('/nix/store/') or len(path.parts) != 4:
        raise ValueError('Invalid closure path')
    shutil.copytree(store_root / entry.lstrip('/'), root / entry.lstrip('/'), symlinks=True)
bcenv = store_root / a.bcenv.lstrip('/') / 'lib/bcenv'
engine = store_root / a.engine.lstrip('/') / 'share'
(root / 'opt/bcenv/seasons/2023').mkdir(parents=True)
shutil.copytree(bcenv / 'runtime', root / 'opt/bcenv/runtime')
shutil.copyfile(bcenv / 'seasons/2023/adapter.py', root / 'opt/bcenv/seasons/2023/adapter.py')
shutil.copytree(bcenv / 'replay-classes', root / 'opt/bcenv/replay-classes')
shutil.copytree(engine / 'battlecode23', root / 'opt/battlecode23')
shutil.copytree(engine / 'doc/battlecode23', workspace / 'reference')
shutil.copytree(source / 'runbooks/local-pilot/skills', workspace / 'skills')
shutil.copytree(source / 'runbooks/local-pilot/docs', workspace / 'docs')
year = json.loads((source / 'seasons/2023/manifest.json').read_text())
(workspace / 'manifest.json').write_text(json.dumps({
    'schema': 'bcenv.local-pilot-manifest.v1', 'season': '2023',
    'engine': year['engine'], 'interface': year['interface'],
    'runtime_package': a.bcenv, 'rules': 'reference/specs/',
    'api': ['reference/api/', 'reference/api-source/'],
    'skills': ['skills/common/SKILL.md', 'skills/2023/SKILL.md'],
    'tools': 'docs/BC23-LOCAL.md', 'trial': 'trial.json',
    'candidate_supplied': False, 'fixture': 'official inert testplayerempty'}, indent=2) + '\n')
(workspace / 'trial.json').write_text(json.dumps({
    'schema': 'bcenv.local-pilot-spec.v1', 'scope': 'local-only diagnostic',
    'minutes': 20, 'max_matches': 12, 'concurrency': 1,
    'map': '/opt/battlecode23/maps/DefaultMap.map23',
    'fixture_package': 'testplayerempty', 'required_sides': ['A', 'B'],
    'required_work': ['own legal source', 'official verified build', 'both sides',
                      'prediction before own change', 'replay evidence', 'verified source package', 'final report'],
    'writable_paths': ['competitor/', 'work/'], 'network': 'none',
    'replica_required': False, 'viewer_required': False,
    'stop_on': ['workflow complete', 'time limit', 'match limit', 'unrecoverable environment failure'],
    'model_configuration': 'recorded separately by external launcher'}, indent=2) + '\n')
shutil.copyfile(source / 'prompts/2023/local-pilot.txt', out / 'KICKOFF.txt')
(workspace / 'START_HERE.md').write_text('''# Local-only BC23 diagnostic pilot

This pilot is authorized to build an initial bot before the replica is ready.
Read manifest.json, trial.json, docs/BC23-LOCAL.md and both packaged skills.
These files are complete local-pilot instructions; full replica/clean-room
acceptance is a separate task. Follow KICKOFF.txt's local-only task.

All editable files are here under competitor/ and work/. The host-visible path
is this workspace directory. The launcher runs with this directory as /workspace
inside a minimal filesystem; pass relative paths to it. Do not read other host
workspaces or change runtime files. General agent tools are behaviorally
restricted, not security-isolated by this launcher. Runtime commands have no
host filesystem mounts or inherited credentials.

Use ../../bcenv from this directory (or its absolute host-visible path):

    ../../bcenv build --source competitor/src --package mybot --output work/build-001
    ../../bcenv build --source /opt/battlecode23/fixtures/src --package testplayerempty --output work/fixture-build
    ../../bcenv run --team-a work/build-001 --team-b work/fixture-build --map /opt/battlecode23/maps/DefaultMap.map23 --output work/match-001
    ../../bcenv package --build work/build-001 --output work/package-001

Create Java package-relative source including RobotPlayer.java. Its entry point
is public static void run(battlecode.common.RobotController rc). Read reference/
for packaged rules/specs and API, and skills/ for workflow. Choose your own
strategy. The fixture is deliberately inert and does not establish strength.
Swap --team-a and --team-b for the second match. Every output directory must be
new. Local replay-summary.json contains round/event evidence and explicit limits.
Exact process logs and result.json distinguish failure, unknown outcome and win.

No installs or network calls are needed. No replica/submit operation is available
or required for this pilot. Budget: 20 minutes, at most 12 games, sequential
execution, 120-second default per-process timeout. Stop on unrecoverable setup
failure rather than editing the environment or requesting coaching.
''')
launcher = root / 'opt/launch.py'
launcher.write_text('''import os, sys
os.chdir('/workspace')
os.environ.update(%r)
os.execv(%r, [%r, '/opt/bcenv/seasons/2023/adapter.py'] + sys.argv[1:])
''' % ({'HOME': '/workspace', 'BCENV_ENGINE_JAR': '/opt/battlecode23/engine.jar',
        'BCENV_JAVA': a.jdk + '/bin/java', 'BCENV_JAVAC': a.jdk + '/bin/javac',
        'BCENV_REPLAY_CLASSPATH': '/opt/bcenv/replay-classes:/opt/battlecode23/engine.jar'},
        a.python + '/bin/python3', a.python + '/bin/python3'))
(out / 'bcenv').write_text('''#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec env -i PATH=/usr/sbin:/usr/bin:/bin LANG=C /usr/bin/unshare --user --map-root-user --net -- /usr/sbin/chroot "$HERE/root" %s/bin/python3 /opt/launch.py "$@"
''' % a.python)
(out / 'bcenv').chmod(0o755)
files = {}
for path in sorted(out.rglob('*')):
    if path.is_file() and not path.is_symlink():
        # Include actual runtime/content bytes; dependencies are independently
        # immutable store identities and do not need a second giant inventory.
        rel = path.relative_to(out).as_posix()
        if not rel.startswith('root/nix/store/'):
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
(out / 'release.json').write_text(json.dumps({'kind': 'local-only-diagnostic-pilot',
    'fresh_context_required': True, 'agent_isolation': 'behavioral-only',
    'runtime_isolation': 'user-and-network-namespace-chroot-no-host-binds',
    'runtime_store_paths': {'bcenv': a.bcenv, 'engine': a.engine, 'python': a.python, 'jdk': a.jdk},
    'closure': Path(a.closure).read_text().splitlines(), 'files': files}, indent=2) + '\n')
print(out)
