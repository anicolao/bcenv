"""Operator-only public artifact publication. No default Git runner or credentials.

The caller supplies an ordinary Git runner and independently verifies repository
metadata/authorization. A push or readback failure is uncertain: never retry here.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

REPOSITORY_ID = '1431035719'
PUBLIC_REMOTE = 'https://github.com/anicolao/bcenv-artifacts.git'
MAX_BLOB = 49 * 1024 * 1024
MAX_HISTORY = 512 * 1024 * 1024
MAX_FILES = 16384
SCHEMA = 'bcenv.public-retention-inventory.v1'
HEX = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


class PublicationUncertain(RuntimeError):
    """The sole push was attempted; no retry or successful publication inferred."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode() + b'\n'


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _relative(name):
    if not isinstance(name, str) or not name or len(name.encode()) > 1024:
        raise ValueError('invalid inventory path')
    p = PurePosixPath(name)
    if p.is_absolute() or str(p) != name or any(part in ('.', '..', '.git') for part in p.parts):
        raise ValueError('unsafe inventory path')
    if any(ord(c) < 32 or ord(c) == 127 for c in name) or '\\' in name:
        raise ValueError('unsafe inventory path')
    if '.github/workflows' in name or p.name == '.gitattributes':
        raise ValueError('workflow or attribute/filter configuration is not artifact data')
    return p


def _read_bounded(path, size):
    with Path(path).open('rb') as stream:
        value = stream.read(size + 1)
    if len(value) != size:
        raise ValueError('export size changed during bounded read')
    return value


def _exclusive_write(path, value):
    with Path(path).open('xb') as stream:
        stream.write(value)


def validate_inventory(root, inventory):
    """Exact regular-file set and byte hashes; no credentials or mutable export."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('export must be a regular directory')
    if set(inventory) != {'schema', 'files'} or inventory['schema'] != SCHEMA:
        raise ValueError('invalid inventory schema')
    files = inventory['files']
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise ValueError('invalid inventory count')
    for name, item in files.items():
        _relative(name)
        if (not isinstance(item, dict) or set(item) != {'sha256', 'bytes'}
                or not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
                or type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_BLOB):
            raise ValueError('invalid or oversized blob')
    if sum(item['bytes'] for item in files.values()) > MAX_HISTORY:
        raise ValueError('checkpoint payload exceeds history cap')
    seen = set()
    for path in root.rglob('*'):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('symlink or special file in export')
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if name not in files:
            raise ValueError('unlisted export file')
        seen.add(name)
        item = files[name]
        if path.stat().st_size != item['bytes']:
            raise ValueError('export size mismatch')
        value = _read_bounded(path, item['bytes'])
        if len(value) != item['bytes'] or _sha(value) != item['sha256']:
            raise ValueError('export hash mismatch')
        if value.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
            raise ValueError('LFS pointers are not retained payloads')
    if seen != set(files):
        raise ValueError('missing export file')
    encoded = _canonical(inventory)
    if len(encoded) > MAX_BLOB:
        raise ValueError('inventory too large')
    return encoded


def publish_checkpoint(checkout, export_root, inventory, checkpoint_id, branch,
                       expected_old_commit, readback_dir, *, git_runner,
                       repository_id, remote_url=PUBLIC_REMOTE):
    """One CAS publication then exact remote readback, or fail without retry.

    git_runner(argv, cwd=Path) returns stdout bytes and raises on any nonzero exit.
    No shell, subprocess default, credentials, automatic fetch/rebase/retry or
    candidate-facing entrypoint is provided. repository_id is the operator's
    externally verified assertion, not metadata authenticated by this function.
    A normal commit is made in a clean operator-owned artifact checkout only.
    The dedicated archive must have exactly this one branch and no tags. Limits
    cover reachable Git objects, not provider-inaccessible deleted refs/storage.
    Any interruption during push/readback remains unknown and must not be retried,
    including BaseException interruptions propagated unchanged.
    Concurrent unrelated-ref creation cannot be atomically prevented by a branch
    lease; pre/post checks fail closed and never repair or delete such refs.
    """
    if remote_url != PUBLIC_REMOTE or type(repository_id) not in (str, int) or str(repository_id) != REPOSITORY_ID:
        raise ValueError('public repository identity assertion required')
    if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', checkpoint_id):
        raise ValueError('invalid checkpoint ID')
    if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9/_-]{0,127}', branch) or '//' in branch or branch.endswith('/'):
        raise ValueError('invalid branch')
    if not HEX.fullmatch(expected_old_commit):
        raise ValueError('expected old commit must be exact')
    checkout, export_root, readback_dir = map(Path, (checkout, export_root, readback_dir))
    if checkout.is_symlink() or not checkout.is_dir() or readback_dir.exists() or readback_dir.is_symlink():
        raise ValueError('owned checkout and fresh readback directory required')
    if export_root.is_symlink():
        raise ValueError('symlink export root')
    checkout = checkout.resolve()
    export_root = export_root.resolve()
    if (readback_dir.resolve().is_relative_to(checkout) or readback_dir.resolve().is_relative_to(export_root)
            or export_root.is_relative_to(checkout)):
        raise ValueError('export/readback must be outside checkout')
    encoded = validate_inventory(export_root, inventory)
    prefix = 'checkpoints/' + checkpoint_id
    destination = checkout / prefix
    parent = destination.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise ValueError('checkpoint parent must be a regular directory')
    if destination.exists() or destination.is_symlink():
        raise ValueError('checkpoint namespace already exists')
    manifest_name = 'checkpoint-inventory.json'
    if manifest_name in inventory['files']:
        raise ValueError('reserved manifest name')

    def git(args, cwd=checkout, limit=16 * 1024 * 1024):
        value = git_runner(['git', '--no-replace-objects'] + list(args), cwd=cwd)
        if not isinstance(value, bytes) or len(value) > limit:
            raise ValueError('invalid or oversized Git response')
        return value

    def history(commit):
        objects = git(['rev-list', '--objects', '--no-object-names', commit]).splitlines()
        if len(objects) > 100000 or len(objects) != len(set(objects)):
            raise ValueError('history object inventory exceeds bounds')
        if commit.encode() not in objects:
            raise ValueError('history omitted requested commit')
        total = 0
        for raw in objects:
            oid = raw.decode('ascii')
            if not HEX.fullmatch(oid):
                raise ValueError('invalid object ID')
            kind = git(['cat-file', '-t', oid], limit=32).strip()
            size = int(git(['cat-file', '-s', oid], limit=32))
            if size < 0 or kind not in (b'blob', b'tree', b'commit', b'tag'):
                raise ValueError('invalid history object')
            if kind == b'blob' and size > MAX_BLOB:
                raise ValueError('historical blob exceeds cap')
            total += size
            if total > MAX_HISTORY:
                raise ValueError('entire reachable history exceeds cap')
        return total

    def tree(commit, cwd=checkout):
        entries = {}
        for row in git(['ls-tree', '-r', '-z', commit], cwd=cwd).split(b'\0'):
            if not row:
                continue
            info, rawname = row.split(b'\t', 1)
            mode, kind, oid = info.split()
            name = rawname.decode('utf-8')
            _relative(name)
            if mode not in (b'100644', b'100755') or kind != b'blob' or not HEX.fullmatch(oid.decode()):
                raise ValueError('symlink, submodule or invalid tree entry')
            if name in entries:
                raise ValueError('duplicate tree path')
            entries[name] = oid.decode()
        return entries

    def verify_tree(commit, cwd=checkout, materialize=None):
        entries = tree(commit, cwd)
        wanted = {prefix + '/' + name: item for name, item in inventory['files'].items()}
        wanted[prefix + '/' + manifest_name] = {'bytes': len(encoded), 'sha256': _sha(encoded)}
        if {name for name in entries if name.startswith(prefix + '/')} != set(wanted):
            raise ValueError('committed checkpoint file set mismatch')
        for name, item in wanted.items():
            value = git(['cat-file', 'blob', entries[name]], cwd=cwd, limit=MAX_BLOB)
            if len(value) != item['bytes'] or _sha(value) != item['sha256']:
                raise ValueError('committed checkpoint bytes mismatch')
            relative = name[len(prefix) + 1:]
            if materialize is not None and relative != manifest_name:
                target = materialize / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                _exclusive_write(target, value)

    graft_path = Path(git(['rev-parse', '--git-path', 'info/grafts']).decode().strip())
    if not graft_path.is_absolute():
        graft_path = checkout / graft_path
    if graft_path.exists() or graft_path.is_symlink():
        raise ValueError('legacy graft ancestry is not permitted')
    if git(['rev-parse', '--is-shallow-repository']).strip() != b'false':
        raise ValueError('complete non-shallow artifact history required')
    if git(['status', '--porcelain', '--untracked-files=all']):
        raise ValueError('artifact checkout must be clean')
    if git(['rev-parse', 'HEAD']).decode().strip() != expected_old_commit:
        raise ValueError('local expected-old mismatch')
    old_tree = tree(expected_old_commit)
    if git(['log', '-1', '--format=%H', expected_old_commit, '--', prefix]).strip():
        raise ValueError('checkpoint ID was already used in history')
    before_bytes = history(expected_old_commit)
    ref = 'refs/heads/' + branch
    remote = git(['ls-remote', '--refs', remote_url]).decode().splitlines()
    if remote != [expected_old_commit + '\t' + ref]:
        raise ValueError('remote expected-old mismatch')
    destination.mkdir(parents=True, exist_ok=False)
    for name in sorted(inventory['files']):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        item = inventory['files'][name]
        value = _read_bounded(export_root / name, item['bytes'])
        if _sha(value) != item['sha256']:
            raise ValueError('export changed during copy')
        _exclusive_write(target, value)
    validate_inventory(destination, inventory)
    _exclusive_write(destination / manifest_name, encoded)
    names = [prefix + '/' + name for name in sorted(inventory['files'])] + [prefix + '/' + manifest_name]
    git(['add', '--'] + names)
    # Ordinary commit: never bypass hooks or use low-level commit-tree.
    git(['commit', '-m', 'Retain public checkpoint ' + checkpoint_id])
    commit = git(['rev-parse', 'HEAD']).decode().strip()
    if not HEX.fullmatch(commit):
        raise ValueError('invalid created commit')
    if git(['rev-list', '--parents', '-n', '1', commit]).decode().strip().split() != [commit, expected_old_commit]:
        raise ValueError('publication must be one direct descendant')
    new_tree = tree(commit)
    if {name: oid for name, oid in new_tree.items() if not name.startswith(prefix + '/')} != old_tree:
        raise ValueError('commit changed prior checkpoint or unrelated files')
    if git(['status', '--porcelain', '--untracked-files=all']):
        raise ValueError('commit left unaccounted workspace changes')
    verify_tree(commit)
    history_bytes = history(commit)
    if git(['ls-remote', '--refs', remote_url]).decode().splitlines() != [expected_old_commit + '\t' + ref]:
        raise ValueError('remote refs changed before publication')
    # The expected-old lease closes the remote check/push race; the new commit
    # has already been proven a direct descendant, not a history rewrite.
    try:
        git(['push', '--force-with-lease=' + ref + ':' + expected_old_commit,
             remote_url, commit + ':' + ref])
        if git(['ls-remote', '--refs', remote_url]).decode().splitlines() != [commit + '\t' + ref]:
            raise ValueError('unexpected remote refs after publication')
        readback_dir.mkdir(parents=True, exist_ok=False)
        git(['init', '--quiet'], cwd=readback_dir)
        git(['fetch', '--no-tags', remote_url, commit], cwd=readback_dir)
        if git(['rev-parse', 'FETCH_HEAD'], cwd=readback_dir).decode().strip() != commit:
            raise ValueError('remote readback commit mismatch')
        verified_export = readback_dir / 'verified-export'
        verified_export.mkdir(exist_ok=False)
        verify_tree(commit, readback_dir, materialize=verified_export)
        validate_inventory(verified_export, inventory)
    except Exception as error:
        raise PublicationUncertain('sole push/readback did not establish publication; no retry') from error
    return {'schema': 'bcenv.public-retention-publication.v1', 'status': 'verified',
            'remote_url': remote_url, 'repository_id_operator_assertion': str(repository_id),
            'branch': branch, 'previous_commit': expected_old_commit, 'commit': commit,
            'checkpoint_id': checkpoint_id, 'inventory_sha256': _sha(encoded),
            'files': len(inventory['files']), 'history_bytes_before': before_bytes,
            'history_bytes': history_bytes, 'readback_commit': commit,
            'readback_directory': str(readback_dir), 'verified_export': 'verified-export',
            'readback_mode': 'exact Git objects plus verified payload materialization; no checkout/hooks',
            'push_attempts': 1}
