"""Operator-only shared launcher; probe the fixed worker before mailbox readiness.

Use this exact entry point/interpreter for integration and live launch. A probe
is not an end-to-end integration test or a hard-isolation attestation.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from runtime.artifacts import digest, write
from services import local_trial
from runtime.cancellation import cancelled
from services.operator_stop import validate_control_args, SCHEMA as STOP_SCHEMA, MAX_BYTES as STOP_MAX_BYTES, POLL_SECONDS, JOIN_SECONDS

MAX_LOG = 65536


def probe(command, output, timeout=30, *, deadline_monotonic=None, cancel_fd=None):
    """Same inherited environment/cwd as AdapterWorker, bounded diagnostic pipe."""
    start = time.monotonic()
    result = {'command': command + ['--help'], 'status': 'failed', 'exit_code': None}
    def stop_reason():
        if cancelled(cancel_fd):
            return 'cancelled'
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return 'timeout'
        return None
    with Path(output).open('xb') as log:
        reason = stop_reason()
        if reason is not None:
            result.update(status=reason, not_started=True, log=str(output), log_sha256=digest(output))
            return result
        try:
            p = subprocess.Popen(result['command'], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            result['launch_error'] = str(exc)
        else:
            count = 0
            status = None
            try:
                with selectors.DefaultSelector() as sel:
                    sel.register(p.stdout, selectors.EVENT_READ)
                    while sel.get_map():
                        reason = stop_reason()
                        if reason is not None or time.monotonic() - start >= timeout:
                            status = reason or 'timeout'
                            break
                        for key, _ in sel.select(.05):
                            chunk = os.read(key.fileobj.fileno(), 8192)
                            if not chunk:
                                sel.unregister(key.fileobj)
                                continue
                            log.write(chunk[:max(0, MAX_LOG - count)])
                            count += len(chunk)
                            if count > MAX_LOG:
                                status = 'output_limit'
                                break
                        if status:
                            break
                    if status is None:
                        try:
                            remaining = timeout - (time.monotonic() - start)
                            if deadline_monotonic is not None:
                                remaining = min(remaining, deadline_monotonic - time.monotonic())
                            reason = stop_reason()
                            if reason is not None:
                                status = reason
                            elif cancel_fd is None:
                                p.wait(timeout=max(.001, remaining))
                            else:
                                # stdout EOF is not process exit. Keep observing
                                # cancellation without changing the legacy wait.
                                while True:
                                    reason = stop_reason()
                                    remaining = timeout - (time.monotonic() - start)
                                    if deadline_monotonic is not None:
                                        remaining = min(remaining, deadline_monotonic - time.monotonic())
                                    if reason is not None or remaining <= 0:
                                        status = reason or 'timeout'
                                        break
                                    try:
                                        p.wait(timeout=min(.05, remaining))
                                        break
                                    except subprocess.TimeoutExpired:
                                        continue
                        except subprocess.TimeoutExpired:
                            status = 'timeout'
            finally:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()
                p.stdout.close()
            result.update(exit_code=p.returncode,
                          status=status or ('succeeded' if p.returncode == 0 else 'failed'))
    result.update(log=str(output), log_sha256=digest(output))
    return result


def launch_identity(config, *, operator_stop=False):
    options = local_trial.trial_options(config)
    bounds = local_trial.resolve_profile(options['profile'], **{k: v for k, v in options.items() if k not in ('profile', 'validation_games')})
    repo = Path(__file__).resolve().parents[1]
    executable = Path(config['command'][0])
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError('Worker executable must be an existing absolute path')
    return {
        'launcher_sha256': digest(__file__),
        'python': str(Path(sys.executable).resolve()), 'python_sha256': digest(sys.executable),
        'cwd': str(Path.cwd().resolve()),
        'service_code': {str(p.relative_to(repo)): digest(p) for name in ('services', 'runtime')
                         for p in sorted((repo / name).rglob('*.py'))},
        'worker_command': config['command'], 'worker_executable_sha256': digest(executable),
        'games': config['games'], 'timeout': config.get('timeout', 90),
        'match_timeout': config.get('match_timeout', config.get('timeout', 90)),
        'require_experiment_notes': bounds['require_experiment_notes'],
        **({'resolved_profile': dict(bounds)} if bounds['profile'] == local_trial.CONTINUITY else {}),
        'validation_games': config.get('validation_games'),
        'max_run_seconds': bounds['max_run_seconds'],
        **({'operator_stop': {'schema': STOP_SCHEMA, 'max_bytes': STOP_MAX_BYTES,
            'poll_seconds': POLL_SECONDS, 'join_check_seconds': JOIN_SECONDS}} if operator_stop else {}),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', required=True)
    ap.add_argument('--root', required=True)
    ap.add_argument('--mailbox', required=True)
    ap.add_argument('--evidence', required=True, help='Fresh operator-only directory')
    ap.add_argument('--expect-parity', help='Successful integration launch.json')
    ap.add_argument('--check-only', action='store_true')
    ap.add_argument('--operator-control', help='Fresh private operator directory, outside candidate mailbox/artifacts')
    ap.add_argument('--run-id', help='32 lowercase hex run UUID paired with --operator-control')
    args = ap.parse_args(argv)
    control = validate_control_args(args.operator_control, args.run_id)
    config = local_trial.read_json(args.config)
    worker = local_trial.AdapterWorker(config['command'],
        [local_trial.DevelopmentGame(**g) for g in config['games']], config.get('timeout', 90),
        match_timeout=config.get('match_timeout', local_trial.DEFAULT_MATCH_TIMEOUT))
    identity = launch_identity(config, operator_stop=control is not None)
    evidence = Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    record = {'schema': 'bcenv.operator-launch.v1', 'identity': identity,
              'config': config, 'config_sha256': digest(args.config),
              'check_only': args.check_only, 'status': 'parity_failed',
              'limitation': 'Probe does not establish end-to-end integration, confinement or performance.'}
    if control is not None:
        record['operator_control'] = {'directory': str(control[0]), 'run_id': control[1],
            'parity_exclusion': 'Run-specific path and UUID differ intentionally; mode/schema/limits remain bound.'}
    if args.expect_parity:
        prior = local_trial.read_json(args.expect_parity)
        if prior.get('status') != 'probe_passed' or prior.get('identity') != identity:
            write(evidence / 'launch.json', record)
            return 1
    record['probe'] = probe(worker.command, evidence / 'worker-probe.log')
    record['status'] = 'probe_passed' if record['probe']['status'] == 'succeeded' else 'probe_failed'
    write(evidence / 'launch.json', record)
    if record['status'] != 'probe_passed':
        return 1
    if args.check_only:
        return 0
    # Reuse the validated config snapshot and exact same process context; do not
    # re-read a config or start a different Nix/python wrapper after probing.
    trial = local_trial.LocalTrial(args.root, worker, **local_trial.trial_options(config))
    with local_trial.cooperative_signals(trial):
        local_trial.run_mailbox(trial, args.mailbox, operator_control=args.operator_control, run_id=args.run_id)
    return 0


if __name__ == '__main__':
    sys.exit(main())
