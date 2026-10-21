#!/usr/bin/env python3
"""Trusted operator: export public local-check tooling into a fresh candidate root."""
import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.artifacts import digest, inventory, write


def export(workspace, launch_prefix=None):
    if launch_prefix is not None:
        if (not isinstance(launch_prefix, list) or not launch_prefix or
                any(not isinstance(x, str) or not x or '\x00' in x for x in launch_prefix)):
            raise ValueError('Launch prefix must be a nonempty JSON argv array')
        if not Path(launch_prefix[0]).is_absolute() or not Path(launch_prefix[0]).is_file():
            raise ValueError('Launch prefix executable must be an existing absolute path')
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Workspace must exist')
    runtime = {key: str(Path(os.environ[key]).resolve(strict=True))
               for key in ('BCENV_ENGINE_JAR', 'BCENV_JAVA', 'BCENV_JAVAC')}
    python = str(Path(sys.executable).resolve())
    if (root / 'candidate-check').exists() or (root / 'candidate-check').is_symlink():
        raise FileExistsError('Candidate launcher already exists')
    target = root / '.candidate-check'
    target.mkdir(exist_ok=False)
    for rel in ('tools/candidate_check.py', 'seasons/2023/adapter.py', 'runtime/artifacts.py'):
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, destination)
    manifest = {'schema': 'bcenv.candidate-check-export.v1', 'workspace': str(root),
                'runtime': runtime, 'runtime_sha256': {k: digest(v) for k, v in runtime.items()},
                'python': python, 'files': inventory(target)}
    # The launcher pins runtime paths and root without asking the competitor to
    # inspect evaluator files. All copied code is public, generic development code.
    launcher = target / 'inner-launcher' if launch_prefix else root / 'candidate-check'
    with launcher.open('x') as f:
        f.write('#!' + python + '\nimport hashlib, os, pathlib, runpy, sys\n')
        f.write('pins = ' + repr({runtime[k]: v for k, v in manifest['runtime_sha256'].items()}) + '\n')
        f.write('if any(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() != h for p, h in pins.items()):\n    sys.exit("Pinned runtime changed")\n')
        f.write('os.environ.update(' + repr(runtime) + ')\n')
        f.write('module = runpy.run_path(' + repr(str(target / 'tools/candidate_check.py')) + ')\n')
        f.write('sys.exit(module["main"](workspace=' + repr(str(root)) + '))\n')
    launcher.chmod(0o755)
    manifest['inner_launcher_sha256'] = digest(launcher)
    if launch_prefix:
        outer = root / 'candidate-check'
        with outer.open('x') as f:
            f.write('#!/bin/sh\nexec ' + shlex.join(launch_prefix + [str(launcher)]) + ' "$@"\n')
        outer.chmod(0o755)
        manifest['launch_prefix'] = launch_prefix
        manifest['launch_prefix_executable_sha256'] = digest(launch_prefix[0])
        manifest['outer_launcher_sha256'] = digest(outer)
    write(target / 'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--launch-prefix-json', help='Trusted operator fixed argv prefix for relocated runtimes')
    args = parser.parse_args()
    prefix = json.loads(args.launch_prefix_json) if args.launch_prefix_json else None
    print(json.dumps(export(args.workspace, prefix), sort_keys=True))
