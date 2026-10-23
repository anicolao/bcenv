"""Explicit epoch-bound cooperative file RPC; not authentication or recovery.

Canonical replies are committed by trial.request before file delivery. No retry,
process control, implicit current-epoch adoption, or filesystem authority fallback.
The owner, not this transport loop, cancels/joins workers and publishes cleanup.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

SCHEMA = 'bcenv.resumable-rpc.v1'
MAX_REQUEST = 1_500_000
MAX_RESPONSE = 65_536
MAX_PENDING = 32
MAX_OBSERVATIONS = 2200  # Per epoch transport work, not a fresh SQL allowance.
_ID = re.compile(r'[0-9a-f]{32}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


class TransportRejected(ValueError):
    """A noncanonical transport denial; it contains no trial result or receipt."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _decode(data):
    return json.loads(data, object_pairs_hook=_unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('invalid_number')))


def _identity(authority_id, epoch):
    if (type(authority_id) is not str or not _ID.fullmatch(authority_id) or
            type(epoch) is not int or epoch < 0):
        raise ValueError('invalid_epoch_identity')


def _directory(path):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('mailbox_symlink')
    if not path.is_dir():
        raise ValueError('mailbox_not_ready')
    return path


def _read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ValueError('file_limit')
        data = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    if (len(data) != before.st_size or len(data) > limit or
            (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
        raise ValueError('file_changed')
    return data


def _publish(path, data):
    # No overwrite/retry after uncertain publication. A duplicate durable ingress
    # is the authority's decision; this helper never dispatches it a second time.
    if path.exists():
        raise ValueError('response_already_present')
    temporary = path.parent / (path.stem + '.tmp-' + uuid.uuid4().hex)
    with temporary.open('xb') as handle:
        if handle.write(data) != len(data):
            raise OSError('short_publication')
    os.replace(temporary, path)


def _wrapper(authority_id, epoch, request_id, body_hash, *, response=None, rejection=None):
    result = dict(schema=SCHEMA, authority_id=authority_id, epoch=epoch,
                  request_id=request_id, body_sha256=body_hash)
    if rejection is not None:
        result.update(canonical=False, transport_error=rejection)
    else:
        result.update(canonical=True, response=response)
    return result


def mailbox_request(mailbox, body, *, authority_id, epoch, timeout=150, request_id=None):
    """One explicitly pinned request. Lost delivery is uncertain, never resent."""
    _identity(authority_id, epoch)
    if type(timeout) not in (int, float) or not 0 < timeout <= 21600:
        raise ValueError('invalid_timeout')
    mailbox = _directory(mailbox)
    requests, responses = _directory(mailbox/'requests'), _directory(mailbox/'responses')
    if (mailbox/'stopped.json').exists():
        raise ValueError('mailbox_stopped')
    rid = uuid.uuid4().hex if request_id is None else request_id
    if type(rid) is not str or not _ID.fullmatch(rid):
        raise ValueError('invalid_request_id')
    body_hash = digest(body)
    envelope = dict(schema=SCHEMA, authority_id=authority_id, epoch=epoch,
                    request_id=rid, body_sha256=body_hash, body=body)
    payload = encoded(envelope)
    if len(payload) > MAX_REQUEST:
        raise ValueError('request_limit')
    # Explicit ID reuse must not consume a stale response or overwrite a request.
    if (requests/(rid+'.json')).exists() or (responses/(rid+'.json')).exists():
        raise ValueError('request_id_already_present')
    temporary = requests/(rid+'.tmp')
    with temporary.open('xb') as handle:
        if handle.write(payload) != len(payload):
            raise OSError('short_request_write')
    os.replace(temporary, requests/(rid+'.json'))
    deadline = time.monotonic() + timeout
    response_path = responses/(rid+'.json')
    while time.monotonic() < deadline:
        try:
            data = _read(response_path, MAX_RESPONSE)
        except FileNotFoundError:
            time.sleep(min(.05, max(0, deadline-time.monotonic())))
            continue
        result = _decode(data)
        expected = dict(schema=SCHEMA, authority_id=authority_id, epoch=epoch,
                        request_id=rid, body_sha256=body_hash)
        if (type(result) is not dict or type(result.get('epoch')) is not int or
                any(result.get(key) != value for key, value in expected.items())):
            raise ValueError('response_binding_mismatch')
        if result.get('canonical') is not True:
            raise TransportRejected(result.get('transport_error', 'transport_rejected'))
        if set(result) != set(expected) | {'canonical', 'response'} or type(result.get('response')) is not dict:
            raise ValueError('response_schema')
        return result['response']
    raise TimeoutError('Request outcome uncertain; do not resend. Request ID: '+rid)


def _prepare(name, raw, authority_id, epoch):
    """Return bound ID/body/hash/rejection. Malformed inputs become charged denials."""
    filename_id = name[:-5] if name.endswith('.json') else ''
    rid = filename_id if _ID.fullmatch(filename_id) else hashlib.sha256(os.fsencode(name)+b'\0'+raw).hexdigest()[:32]
    raw_hash = hashlib.sha256(os.fsencode(name)+b'\0'+raw).hexdigest()
    invalid = {'operation': 'invalid_request', 'transport_sha256': raw_hash}
    try:
        value = _decode(raw)
        if type(value) is not dict:
            raise ValueError('invalid_envelope')
        # An explicitly foreign generation never becomes a current request/debit.
        if ('authority_id' in value and value['authority_id'] != authority_id or
                'epoch' in value and (type(value['epoch']) is not int or value['epoch'] != epoch)):
            body_hash = digest(value.get('body'))
            return rid, None, body_hash, 'epoch_identity_mismatch'
        expected = {'schema','authority_id','epoch','request_id','body_sha256','body'}
        body_hash = digest(value.get('body'))
        if (set(value) != expected or value['schema'] != SCHEMA or
                value['request_id'] != rid or not _ID.fullmatch(filename_id) or
                type(value['body_sha256']) is not str or not _SHA.fullmatch(value['body_sha256']) or
                value['body_sha256'] != body_hash or type(value['body']) is not dict):
            return rid, invalid, body_hash, None
        return rid, value['body'], body_hash, None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return rid, invalid, raw_hash, None


def serve_mailbox(trial, mailbox, *, poll_seconds=.05):
    """Serve only this epoch; caller owns finally-based cancel/join/receipt."""
    authority_id, epoch = trial.authority.authority_id, trial.epoch
    _identity(authority_id, epoch)
    if type(poll_seconds) not in (int, float) or not 0 < poll_seconds <= 1:
        raise ValueError('invalid_poll')
    mailbox = Path(mailbox).absolute()
    _directory(mailbox.parent)
    if mailbox.is_symlink():
        raise ValueError('mailbox_symlink')
    mailbox.mkdir(exist_ok=False)
    requests, responses = mailbox/'requests', mailbox/'responses'
    requests.mkdir(); responses.mkdir()
    _publish(mailbox/'ready.json', encoded(dict(schema=SCHEMA, authority_id=authority_id, epoch=epoch)))
    processed = rejected = observed = 0
    while (not trial.stop_requested.is_set() and trial.remaining_seconds() > 0 and
           trial.current_epoch_active()):
        _directory(mailbox); _directory(requests); _directory(responses)
        pending = sorted(requests.iterdir())
        if len(pending) > MAX_PENDING:
            raise ValueError('mailbox_queue_limit')
        handled = False
        for path in pending:
            if trial.stop_requested.is_set() or trial.remaining_seconds() <= 0 or not trial.current_epoch_active():
                break
            if re.fullmatch(r'[0-9a-f]{32}\.tmp', path.name):
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REQUEST:
                    raise ValueError('invalid_staging')
                continue
            handled = True
            if observed >= MAX_OBSERVATIONS:
                raise ValueError('transport_observation_limit')
            observed += 1
            trial.storage_checkpoint('mailbox_poll')
            try:
                raw = _read(path, MAX_REQUEST)
            except FileNotFoundError:
                continue
            except (ValueError, OSError) as error:
                # ELOOP means our O_NOFOLLOW rejected a link, a known invalid
                # request type. Other IO failures are infrastructure uncertainty:
                # propagate without inventing a canonical ingress/debit.
                if isinstance(error, OSError) and error.errno != errno.ELOOP:
                    raise
                info = path.lstat()
                raw = encoded({'invalid_file': True, 'mode':info.st_mode, 'size':info.st_size})
            rid, body, body_hash, rejection = _prepare(path.name, raw, authority_id, epoch)
            if rejection is not None:
                response = _wrapper(authority_id, epoch, rid, body_hash, rejection=rejection)
                rejected += 1
            else:
                canonical = trial.request(rid, body)
                response = _wrapper(authority_id, epoch, rid, body_hash, response=canonical)
                processed += 1
            data = encoded(response)
            if len(data) > MAX_RESPONSE:
                raise ValueError('response_limit')
            _publish(responses/(rid+'.json'), data)
            path.unlink()
        if not handled:
            trial.stop_requested.wait(poll_seconds)
    return {'processed':processed, 'transport_rejections':rejected, 'transport_observations':observed,
            'origin_epoch':epoch}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mailbox', required=True)
    parser.add_argument('--authority-id', required=True)
    parser.add_argument('--epoch', required=True, type=int)
    parser.add_argument('--request-json', required=True, help='JSON body file; no source/path inference')
    parser.add_argument('--timeout', type=float, default=150)
    args = parser.parse_args(argv)
    body = _decode(_read(args.request_json, MAX_REQUEST))
    print(json.dumps(mailbox_request(args.mailbox, body, authority_id=args.authority_id,
                                    epoch=args.epoch, timeout=args.timeout), sort_keys=True))


if __name__ == '__main__':
    main()
