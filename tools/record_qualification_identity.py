#!/usr/bin/env python3
"""Bind already-produced qualification evidence; never build or run a fixture."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def record(root, head, revision, tree, head_tree, source_root=None):
    root = Path(root)
    for value in (head, revision, tree, head_tree):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
            raise ValueError('invalid Git identity')
    if head_tree != tree:
        raise ValueError('PR head and tested merge trees differ')
    selections = {name: json.loads((root / (name + '-selection.json')).read_text()) for name in ('active', 'diagnostic')}
    if selections['active'].get('selected') is not True or selections['active'].get('status') != 'selected':
        raise ValueError('active qualification not selected')
    if selections['diagnostic'].get('selected') is not False or selections['diagnostic'].get('status') != 'not_selected':
        raise ValueError('duplicate/ambiguous diagnostic selection')
    raw = (root / 'cooperative-qualification.json').read_bytes()
    evidence = json.loads(raw)
    expected = ['packaged_fd_controls', 'source_nested_sigint', 'source_sync_sigint',
                'source_nested_sigterm', 'source_receipt_failure']
    if evidence['status'] != 'passed_five_narrow_cases' or [c['name'] for c in evidence['cases']] != expected:
        raise ValueError('qualification did not complete the single expected sequence')
    if any(c['status'] != 'passed_with_explicit_scope_limits' for c in evidence['cases']):
        raise ValueError('incomplete case evidence')
    if evidence['cases'][0].get('wrapper_calls') != 3 or evidence['cases'][0].get('new_query_jvms') != 1:
        raise ValueError('unexpected packaged invocation counts')
    if any(c.get('source_adapter_invocations') != 1 or c.get('inner_helpers') != 1 for c in evidence['cases'][1:]):
        raise ValueError('unexpected source invocation counts')
    runtime = json.loads((root / 'store-path.json').read_text())
    if len(runtime) != 1:
        raise ValueError('ambiguous tested runtime')
    runtime_path = next(iter(runtime))
    packaged = evidence['cases'][0]['identity']
    if packaged['wrapper'] != runtime_path + '/bin/bcenv' or packaged['installed'] != packaged['source']:
        raise ValueError('runtime/source identity mismatch')
    source_root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
    source_paths = ('runtime/artifacts.py', 'runtime/cancellation.py', 'services/local_trial.py', 'seasons/2023/adapter.py')
    expected_source = {p: hashlib.sha256((source_root / p).read_bytes()).hexdigest() for p in source_paths}
    if evidence['source_files'] != expected_source or evidence['fixture_sha256'] != hashlib.sha256((source_root / 'tests/cooperative_runtime_qualification.py').read_bytes()).hexdigest():
        raise ValueError('tested checkout source identity mismatch')
    if packaged['source'] != {p: expected_source[p] for p in source_paths if p != 'services/local_trial.py'}:
        raise ValueError('packaged/checkout source identity mismatch')
    data = {'schema': 'bcenv.cooperative-qualification-identity.v1',
            'pr_head': head, 'pr_head_tree': head_tree, 'tested_revision': revision, 'tested_tree': tree,
            'selection': selections,
            'prior_deviation': {'head': 'cccec35e0814471556b26b9ac30ae70772b23e63',
                'ci': 39853320731, 'harnesses': 2, 'cases': 10,
                'description': 'Prior active plus diagnostic instantiation exceeded single-harness prospective scope; its two records remain preserved.'},
            'qualification_sha256': hashlib.sha256(raw).hexdigest(),
            'runtime': runtime, 'source_files': evidence['source_files'],
            'fixture_sha256': evidence['fixture_sha256'],
            'qualification': evidence,
            'scope': 'one active check sequence; diagnostic check explicitly not selected; no VM/PTY or012 quiescence claim'}
    # Exactly one authoritative publication; partial/error is not overwritten.
    with (root / 'qualification-identity.json').open('x') as output:
        json.dump(data, output, indent=2, sort_keys=True)
        output.write('\n')
    return data


def git_identities(head):
    if not re.fullmatch(r'[0-9a-f]{40}', head or ''):
        raise ValueError('invalid Git head before lookup/fetch')
    # checkout may be shallow: one bounded exact-SHA read-only fetch, never a build.
    present = subprocess.run(['git', 'cat-file', '-e', head + '^{commit}'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    if present.returncode:
        subprocess.run(['git', 'fetch', '--no-tags', '--depth=1', 'origin', head], check=True, timeout=30)
    def rev(value):
        return subprocess.check_output(['git', 'rev-parse', value], text=True, timeout=10).strip()
    return rev('HEAD'), rev('HEAD^{tree}'), rev(head + '^{tree}')


if __name__ == '__main__':
    head = os.environ['QUALIFICATION_PR_HEAD']
    revision, tree, head_tree = git_identities(head)
    record(Path(sys.argv[1]), head, revision, tree, head_tree)
