"""Operator-only closed evidence verification and fresh-workspace restoration.

This never restores executable service authority or starts/resumes an author.
The approval digest is an operator assertion, not an authentication mechanism.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

SCHEMA = 'bcenv.public-retention-inventory.v1'
MAX_BLOB = 49 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_LOGICAL_FILE = 64 * 1024 * 1024
HEX = re.compile(r'[0-9a-f]{64}\Z')


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _no_symlink_ancestors(path):
    for item in (path.absolute(), *path.absolute().parents):
        if item.is_symlink():
            raise ValueError('symlink_ancestor')


def _read(path, limit):
    with path.open('rb') as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError('file_size_limit')
    return data


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _path(name):
    if (not isinstance(name, str) or not name or '\\' in name or
            '\x00' in name or any(ord(c) < 32 for c in name)):
        raise ValueError('unsafe_path')
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('', '.', '..', '.git', '.github', '.gitattributes') for x in name.split('/')):
        raise ValueError('unsafe_path')
    return p


def verify_closed_export(root, *, approved_inventory_sha256):
    """Verify the exact already-reviewed, bounded file set; never infer approval."""
    root = Path(root)
    _no_symlink_ancestors(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('invalid_export_root')
    if not isinstance(approved_inventory_sha256, str) or not HEX.fullmatch(approved_inventory_sha256):
        raise ValueError('approval_required')
    invpath = root / 'inventory.json'
    if invpath.is_symlink() or not invpath.is_file():
        raise ValueError('inventory_missing')
    raw = _read(invpath, MAX_BLOB)
    if len(raw) > MAX_BLOB or _digest(raw) != approved_inventory_sha256:
        raise ValueError('inventory_binding')
    inv = json.loads(raw, object_pairs_hook=_pairs)
    if (not isinstance(inv, dict) or set(inv) != {'schema', 'files'} or inv.get('schema') != SCHEMA or
            not isinstance(inv.get('files'), dict) or len(inv['files']) > 20000):
        raise ValueError('inventory_schema')
    expected = {'inventory.json'}
    total = len(raw)
    for name, record in inv['files'].items():
        _path(name)
        if name == 'inventory.json' or not isinstance(record, dict) or set(record) != {'bytes', 'sha256'}:
            raise ValueError('inventory_entry')
        size, digest = record.get('bytes'), record.get('sha256')
        if type(size) is not int or not 0 <= size <= MAX_BLOB or not isinstance(digest, str) or not HEX.fullmatch(digest):
            raise ValueError('inventory_entry')
        total += size
        if total > MAX_TOTAL:
            raise ValueError('export_total_limit')
        expected.add(name)
    actual = set()
    for p in root.rglob('*'):
        if p.is_symlink():
            raise ValueError('symlink')
        if p.is_dir():
            continue
        if not p.is_file():
            raise ValueError('nonregular')
        actual.add(p.relative_to(root).as_posix())
    if actual != expected:
        raise ValueError('unexpected_or_missing_file')
    for name, record in inv['files'].items():
        p = root / name
        if p.stat().st_size != record['bytes'] or _digest(_read(p, record['bytes'])) != record['sha256']:
            raise ValueError('content_mismatch')
    return inv


def restore_evidence(root, destination, *, approved_inventory_sha256,
                     expected_authority_id, expected_config_digest):
    """Materialize exact reviewed evidence into a new, non-runnable workspace.

    No remote URL, credentials, authority database, task continuation or process
    execution is accepted. Remote exact-commit verification is a separate gate.
    A failed copy remains visibly partial and is never retried or overwritten.
    """
    root, destination = Path(root), Path(destination)
    _no_symlink_ancestors(destination)
    inv = verify_closed_export(root, approved_inventory_sha256=approved_inventory_sha256)
    evidence = json.loads(_read(root / 'evidence.json', MAX_BLOB), object_pairs_hook=_pairs)
    if (evidence.get('schema') != 'bcenv.public-retention-snapshot.v1' or
            evidence.get('authority_id') != expected_authority_id or
            evidence.get('config_digest') != expected_config_digest or
            not isinstance(expected_authority_id, str) or not expected_authority_id or
            not isinstance(expected_config_digest, str) or not HEX.fullmatch(expected_config_digest)):
        raise ValueError('evidence_identity')
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination_not_fresh')
    destination.mkdir(parents=False)
    for name in ('src', 'tests', 'work', 'recovered'):
        (destination / name).mkdir()
    target = destination / 'recovered'
    for name in sorted(['inventory.json', *inv['files']]):
        source = root / name
        if source.is_symlink():
            raise ValueError('source_changed')
        data = _read(source, MAX_BLOB if name == 'inventory.json' else inv['files'][name]['bytes'])
        digest = approved_inventory_sha256 if name == 'inventory.json' else inv['files'][name]['sha256']
        size = len(data) if name == 'inventory.json' else inv['files'][name]['bytes']
        if len(data) != size or _digest(data) != digest:
            raise ValueError('source_changed')
        output = target / name
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as handle:
            if handle.write(data) != len(data):
                raise OSError('short_write')
    verify_closed_export(target, approved_inventory_sha256=approved_inventory_sha256)
    evidence = json.loads((target / 'evidence.json').read_bytes(), object_pairs_hook=_pairs)
    own_files = evidence.get('own_files', {})
    if not isinstance(own_files, dict):
        raise ValueError('own_files_schema')
    own_root = destination / 'own-files'
    own_root.mkdir()
    materialized_bytes = 0
    for name, record in sorted(own_files.items()):
        _path(name)
        if name.split('/')[0] not in ('sources', 'batches', 'records') or not isinstance(record, dict):
            raise ValueError('own_file_path')
        size, digest, chunks = record.get('bytes'), record.get('sha256'), record.get('chunks')
        if (type(size) is not int or not 0 <= size <= MAX_LOGICAL_FILE or
                not isinstance(digest, str) or not HEX.fullmatch(digest) or
                not isinstance(chunks, list) or len(chunks) > MAX_LOGICAL_FILE // (1024 * 1024) + 1):
            raise ValueError('own_file_schema')
        materialized_bytes += size
        if materialized_bytes > MAX_TOTAL:
            raise ValueError('materialization_limit')
        output = own_root / name
        output.parent.mkdir(parents=True, exist_ok=True)
        h, written = hashlib.sha256(), 0
        with output.open('xb') as handle:
            for chunk in chunks:
                if not isinstance(chunk, str) or not HEX.fullmatch(chunk):
                    raise ValueError('chunk_identity')
                key = 'blobs/' + chunk
                if key not in inv['files']:
                    raise ValueError('uncommitted_chunk')
                data = _read(target / key, 1024 * 1024)
                if _digest(data) != chunk or written + len(data) > size:
                    raise ValueError('chunk_integrity')
                if handle.write(data) != len(data):
                    raise OSError('short_write')
                h.update(data); written += len(data)
        if written != size or h.hexdigest() != digest:
            raise ValueError('materialized_identity')
    receipt = {'schema': 'bcenv.public-evidence-restore.v1',
               'inventory_sha256': approved_inventory_sha256,
               'files': len(inv['files']) + 1,
               'evidence_verified': True, 'service_authority_restored': False,
               'author_resumed': False, 'active_source_restored': False,
               'uncommitted_work_recovered': False}
    with (destination / 'work' / 'RESTORE.json').open('x') as handle:
        json.dump(receipt, handle, sort_keys=True)
        handle.write('\n')
    return receipt
