"""Packaged CLI integration checks against an independent synthetic official replay."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def query(output, *extra, ok=True, replay='synthetic.bc23'):
    run = subprocess.run(['bcenv', 'replay', 'query', '--replay', replay,
        '--round-start', '35', '--round-end', '40', '--robot-id', '43',
        '--limit', '3', '--output', output, *extra], capture_output=True, text=True)
    assert (run.returncode == 0) == ok, (run.returncode, run.stdout, run.stderr)
    return run


before = sha('synthetic.bc23')
query('capped')
result = json.loads(Path('capped/query-result.json').read_text())
data = json.loads(Path('capped/replay-query.json').read_text())
assert result['status'] == 'completed' and result['truncated']
assert result['replay']['sha256'] == data['source_replay']['sha256'] == before
assert data['matching_record_count'] == 26 and data['returned_record_count'] == 3
assert data['truncated'] and not data['selected_records_complete']
assert [r['kind'] for r in data['records']] == ['spawn', 'team_resource_delta', 'team_resource_delta']
assert data['records'][0]['robot_id'] == 43 and data['records'][0]['body_type'] == 'CARRIER'
assert [(r['team_id'], r['adamantium_delta']) for r in data['records'][1:]] == [(2, -35), (1, 35)]
query('complete', '--limit', '26')
data = json.loads(Path('complete/replay-query.json').read_text())
assert not data['truncated'] and data['selected_records_complete']
assert data['records'][-1]['kind'] == 'death' and data['records'][-1]['robot_id'] == 43
assert all(r['round'] in range(35, 41) for r in data['records'])
query('late', '--round-start', '45', '--round-end', '45', '--robot-id', '42', '--limit', '10')
data = json.loads(Path('late/replay-query.json').read_text())
assert data['records'][-1]['raw_target'] == -45
query('empty', '--round-start', '46', '--round-end', '47')
data = json.loads(Path('empty/replay-query.json').read_text())
assert data['records'] == [] and data['selected_records_complete']
assert not data['matches'][0]['all_requested_positive_rounds_present']
for flag, value in [('--round-start', '-1'), ('--round-end', '34'), ('--limit', '0'),
                    ('--limit', '10001'), ('--robot-id', '-1'), ('--round-end', '2147483648')]:
    query('invalid-argument', flag, value, ok=False)
    assert not Path('invalid-argument').exists()
query('capped', ok=False)  # never overwrite an existing query artifact
Path('broken.bc23').write_bytes(b'not a gzip replay')
query('broken', ok=False, replay='broken.bc23')
assert json.loads(Path('broken/query-result.json').read_text())['status'] == 'failed'
assert json.loads(Path('broken/replay-query.json').read_text())['status'] == 'invalid'
Path('truncated.bc23').write_bytes(Path('synthetic.bc23').read_bytes()[:-5])
query('truncated', ok=False, replay='truncated.bc23')
assert json.loads(Path('truncated/replay-query.json').read_text())['status'] == 'invalid'
# Bound decompression before attempting to interpret a FlatBuffer; the tiny
# compressed fixture expands past 64 MiB. A sparse oversized input is rejected
# by the adapter before a directory or JVM is allocated.
with gzip.open('oversized-uncompressed.bc23', 'wb') as stream:
    for _ in range(64):
        stream.write(b'\0' * (1024 * 1024))
    stream.write(b'\0')
query('oversized-uncompressed', ok=False, replay='oversized-uncompressed.bc23')
error = json.loads(Path('oversized-uncompressed/replay-query.json').read_text())
assert error['status'] == 'invalid' and 'uncompressed input limit exceeded' in error['error']
with open('oversized-compressed.bc23', 'wb') as stream:
    stream.truncate(64 * 1024 * 1024 + 1)
query('oversized-compressed', ok=False, replay='oversized-compressed.bc23')
assert not Path('oversized-compressed').exists()
assert sha('synthetic.bc23') == before
Path('query-integration-evidence.json').write_text(json.dumps({
    'status': 'passed', 'fixture_sha256': before, 'source_unchanged': True,
    'checks': ['late raw target', 'exact robot/range filters', 'team deltas retained',
        'cap and exact matching count', 'exact limit', 'empty range coverage',
        'invalid arguments', 'non-overwrite', 'malformed gzip', 'truncated gzip',
        'compressed input cap', 'uncompressed input cap']}, indent=2) + '\n')
print('Packaged replay query integration: PASS')

# Five ordered inert cancellation cases, only on this approved remote check.
# The source fixture is separate from the real packaged CLI used by case1.
import os
import sys
qualification_mode = os.environ['BCENV_REMOTE_COOPERATIVE_QUALIFICATION']
if qualification_mode not in ('0', '1'):
    raise ValueError('invalid cooperative qualification selector')
Path('qualification-selection.json').write_text(json.dumps({
    'schema': 'bcenv.cooperative-qualification-selection.v1',
    'selected': qualification_mode == '1',
    'status': 'selected' if qualification_mode == '1' else 'not_selected'}, sort_keys=True) + '\n')
if qualification_mode == '1':
    sys.path.insert(0, os.environ['BCENV_QUALIFICATION_SOURCE'])
    from tests.cooperative_runtime_qualification import run_remote
    run_remote('cooperative-qualification', 'synthetic.bc23')
elif qualification_mode == '0':
    print('COOPERATIVE_QUALIFICATION_NOT_SELECTED: default-off check instance; no lifecycle cases')
