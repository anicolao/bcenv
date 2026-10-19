#!/usr/bin/env python3
"""Export a bounded source-discovery trial; does not author/build either player."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--opponent-checkout', required=True)
a, rest = p.parse_known_args()
repo = Path(__file__).resolve().parents[1]
opponent = Path(a.opponent_checkout).resolve()
provenance = json.loads((repo / 'seasons/2023/lecture-opponent.json').read_text())
for name, digest in provenance['files'].items():
    if hashlib.sha256((opponent / name).read_bytes()).hexdigest() != digest:
        raise ValueError('Opponent input drift: ' + name)
subprocess.run([sys.executable, str(repo / 'scripts/materialize-local-pilot.py'), *rest], check=True)
out = Path(rest[rest.index('--output') + 1]).resolve()
w = out / 'root/workspace'
for name in ['skills', 'docs']:
    shutil.rmtree(w / name)
    shutil.copytree(repo / 'runbooks/lecture-trial' / name, w / name)
(w / 'sources').mkdir()
archive = w / 'sources/battlecode23-lectureplayer.zip'
with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
    for name in sorted(provenance['files']):
        info = zipfile.ZipInfo(name, (2023, 1, 20, 2, 46, 16))
        info.external_attr = 0o100644 << 16
        z.writestr(info, (opponent / name).read_bytes())
provenance['archive'] = archive.name
provenance['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
(w / 'sources/catalog.json').write_text(json.dumps({'sources': [provenance]}, indent=2) + '\n')
(w / 'sources/README.md').write_text('''# Frozen official source catalog

This source-only mirror contains a Battlecode organization's official educational
opponent. Inspect catalog.json to identify its year, source provenance, package,
license and file/archive hashes. Retrieve a copy under work/opponents/ and build
it yourself. No precompiled opponent is supplied. Only Java files belong in a
bcenv build source root; preserve the license/provenance outside that root.
This mirror supports reproducible offline discovery, not live-web search.
''')
manifest = json.loads((w / 'manifest.json').read_text())
manifest.update(schema='bcenv.lecture-trial-manifest.v1', fixture='readiness-only', opponent_catalog='sources/catalog.json')
(w / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
trial = {
    'schema': 'bcenv.lecture-trial-spec.v1', 'scope': 'local active-opponent diagnostic',
    'minutes': 25, 'max_matches': 12, 'concurrency': 1,
    'development_maps': ['/opt/battlecode23/maps/DefaultMap.map23', '/opt/battlecode23/maps/SmallElements.map23'],
    'required_sides': ['A', 'B'], 'expected_games': 8,
    'required_work': ['opponent discovery/provenance/retrieval/build', 'initial behavior/rules plan before code', 'own legal source',
        'baseline on both maps and sides', 'replay-grounded diagnosis', 'falsifiable hypothesis before focused change',
        'paired retest', 'honest selection', 'verified source package', 'code correctness and limitations report'],
    'writable_paths': ['competitor/', 'work/'], 'network': 'none', 'replica_required': False,
    'stop_on': ['workflow complete', 'time limit', 'match limit', 'unrecoverable environment failure'],
    'model_configuration': {'requested_model': 'gpt-6.1-sol', 'requested_reasoning_effort': 'high', 'fresh_context': True},
    'isolation': 'behavioral agent boundary; namespace-isolated credential-free runtime',
}
(w / 'trial.json').write_text(json.dumps(trial, indent=2) + '\n')
shutil.copyfile(repo / 'prompts/2023/lecture-trial.txt', out / 'KICKOFF.txt')
(w / 'START_HERE.md').write_text('''# Local BC23 active-opponent diagnostic

Read manifest.json, trial.json, both skills and docs/BC23-LOCAL.md. These are the
complete instructions for this local diagnostic, not the full replica trial.
Find the authentic lecture opponent using sources/. Read packaged rules/API in
reference/. Write your own candidate under competitor/src/ and all records,
retrieved opponent source and tool outputs under work/. Do not change other files.

Run ../../bcenv from this workspace. It presents this directory as /workspace in
a minimal credential-free runtime. Pass relative source/output paths. Maps live
at /opt/battlecode23/maps/ inside that runtime. Use the two maps in trial.json.

    ../../bcenv build --source competitor/src --package mybot --output work/build-001
    ../../bcenv run --team-a work/build-001 --team-b work/opponent-build --map /opt/battlecode23/maps/DefaultMap.map23 --output work/match-001
    ../../bcenv package --build work/build-001 --output work/package-001

Source inputs are Java-only package-relative directories. Every output must be
new. Read replay/query semantics in docs before interpreting evidence. No installs
or network calls are needed. No replica or submission endpoint is available.
General host tools are behaviorally restricted, not security-isolated. The runtime
has no inherited credentials or host filesystem binds. Stop on environment failure.
''')
release = json.loads((out / 'release.json').read_text())
release['kind'] = 'local-lecture-opponent-diagnostic'
release['opponent'] = provenance
release['files'] = {
    str(path.relative_to(out)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(out.rglob('*')) if path.is_file() and not path.is_symlink()
    and path != out / 'release.json' and not str(path.relative_to(out)).startswith('root/nix/store/')
}
(out / 'release.json').write_text(json.dumps(release, indent=2) + '\n')
print(out)
