"""Trusted two-phase retention bridge; never a candidate command or auto-retry.

Exports and Git credentials remain operator-side. Independent content approval is
required between preparation and publication. No default Git runner is installed.
"""
import hashlib
import json
from pathlib import Path

from tools.public_retention import verify_closed_export, _read, MAX_BLOB
from tools.public_retention_git import publish_checkpoint
from tools.public_retention_snapshot import export_snapshot


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def pending(authority, epoch):
    descriptor = authority.retention_descriptor()
    if (not isinstance(descriptor, dict) or descriptor.get('epoch') != epoch or
            descriptor.get('authority_id') != authority.authority_id or
            descriptor.get('config_digest') != authority.config_digest):
        raise ValueError('matching_retention_pending_required')
    return descriptor


def _failure(authority, descriptor, reason):
    # Cleanup/evidence notification must not replace the original error. A
    # failure to persist this notice is unknown and cannot authorize a retry.
    try:
        authority.retention_fail(descriptor['epoch'], descriptor['checkpoint_id'],
            descriptor_sha256=descriptor['descriptor_sha256'], reason=reason)
    except BaseException:
        pass


def prepare_checkpoint(authority, epoch, destination, *, approved_sha256,
                       artifact_requests=None, public_replay_sha256=(), own_records=None):
    descriptor = pending(authority, epoch)
    try:
        inventory = export_snapshot(authority.path, destination,
            expected_authority_id=authority.authority_id,
            expected_config_digest=authority.config_digest,
            fixed_inputs_sha256=descriptor['fixed_inputs_sha256'],
            approved_sha256=approved_sha256, artifact_requests=artifact_requests,
            public_replay_sha256=public_replay_sha256, own_records=own_records,
            expected_retention_descriptor_sha256=descriptor['descriptor_sha256'])
        if pending(authority, epoch) != descriptor:
            raise ValueError('retention_changed_during_export')
        return {'schema': 'bcenv.public-retention-prepared.v1',
                'descriptor': descriptor, 'inventory': inventory,
                'inventory_sha256': inventory['files']['inventory.json']['sha256'],
                'evidence_sha256': inventory['files']['evidence.json']['sha256']}
    except BaseException:
        _failure(authority, descriptor, 'export_failed')
        raise


def publish_and_ack(authority, epoch, export_root, prepared, *,
                    approved_inventory_sha256, checkout, expected_old_commit,
                    readback_dir, git_runner, branch='main'):
    """One approved publication/readback then exact trusted ACK, or fail closed.

    A successful return is required to report this invocation retained. Errors
    preserve partial artifacts and are never retried here. The service's durable
    ACK state is authoritative; a lost response requires operator reconciliation.
    """
    descriptor = pending(authority, epoch)
    if (not isinstance(prepared, dict) or prepared.get('schema') != 'bcenv.public-retention-prepared.v1' or
            prepared.get('descriptor') != descriptor or
            prepared.get('inventory_sha256') != approved_inventory_sha256):
        raise ValueError('prepared_approval_binding')
    phase = 'export_failed'
    try:
        inv = verify_closed_export(export_root, approved_inventory_sha256=approved_inventory_sha256)
        root = Path(export_root)
        evidence_bytes = _read(root / 'evidence.json', MAX_BLOB)
        evidence = json.loads(evidence_bytes)
        if (evidence.get('retention') != descriptor or
                digest(evidence_bytes) != prepared['evidence_sha256']):
            raise ValueError('export_descriptor_binding')
        expected_outer = {'schema': inv['schema'], 'files': dict(inv['files'])}
        raw = _read(root / 'inventory.json', MAX_BLOB)
        expected_outer['files']['inventory.json'] = {'sha256': digest(raw), 'bytes': len(raw)}
        if expected_outer != prepared['inventory']:
            raise ValueError('outer_inventory_binding')
        phase = 'publication_unknown'
        result = publish_checkpoint(checkout, root, expected_outer,
            descriptor['checkpoint_id'], branch, expected_old_commit, readback_dir,
            git_runner=git_runner, repository_id=1431035719)
        if (result.get('status') != 'verified' or result.get('commit') != result.get('readback_commit') or
                type(result.get('push_attempts')) is not int or result.get('push_attempts') != 1):
            raise ValueError('readback_not_verified')
        # Bind a path-free typed observation, not a private operator directory.
        observation = {k: result[k] for k in ('schema','status','repository_id_operator_assertion',
            'previous_commit','commit','checkpoint_id','inventory_sha256','files','history_bytes',
            'readback_commit','push_attempts')}
        phase = 'ack_unknown'
        ack = authority.retention_ack(epoch, descriptor['checkpoint_id'],
            descriptor_sha256=descriptor['descriptor_sha256'],
            inventory_sha256=approved_inventory_sha256,
            snapshot_binding_sha256=prepared['evidence_sha256'], repository_id=1431035719,
            commit_sha=result['commit'], readback_sha256=digest(canonical(observation)),
            readback_verified=True)
        if ack.get('status') != 'retained':
            raise ValueError('ack_not_retained')
        return {'schema': 'bcenv.public-retention-operator-result.v1', 'status': 'retained',
                'descriptor': descriptor, 'publication': observation, 'ack': ack}
    except BaseException:
        _failure(authority, descriptor, phase)
        raise
