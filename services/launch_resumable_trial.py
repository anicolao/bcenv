"""Explicit prospective epoch-profile operator CLI; legacy entrypoints unchanged.

Run inside the already selected runtime context. Direct adapter command only;
no automatic restart, author creation, lease failover, or conversion of old roots.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sys

from services.epoch_authority import AuthorityDenied
from services.resumable_authority import ResumableAuthority
from services.resumable_trial import (PROFILE, ResumableTrial, canonical, clock, fixed_inputs,
    identifier, paths_for, publish, recover_committed, run_service)
from services.launch_local_trial import probe
from runtime.artifacts import digest


def read(path, cap=1500000):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > cap:
        raise ValueError('operator_input_limit')
    return json.loads(path.read_bytes())


def implementation():
    import services.resumable_authority as authority
    import services.resumable_mailbox as mailbox
    import services.resumable_trial as trial
    import services.epoch_authority as core
    import services.local_trial as worker
    import services.operator_stop as stop
    import runtime.artifacts as artifacts
    import runtime.cancellation as cancellation
    roots = {'services':Path(__file__).resolve().parent, 'runtime':Path(artifacts.__file__).resolve().parent}
    return {group+'/'+path.name: {'file':str(path.resolve()), 'sha256':digest(path)}
            for group, root in roots.items() for path in sorted(root.glob('*.py'))}


def runtime_environment():
    """Pin the adapter's documented environment overrides across epochs."""
    result = {}
    for name in ('BCENV_ENGINE_JAR','BCENV_JAVA','BCENV_JAVAC'):
        value = os.environ.get(name)
        if type(value) is not str or not Path(value).is_absolute():
            raise ValueError('runtime_environment_missing')
        result[name] = {'path':value,'sha256':digest(value)}
    for name in ('JAVA_TOOL_OPTIONS','_JAVA_OPTIONS','JDK_JAVA_OPTIONS'):
        value = os.environ.get(name)
        result[name] = None if value is None else hashlib.sha256(value.encode()).hexdigest()
    return result


def load(args):
    return ResumableAuthority(args.authority, authority_id=args.authority_id,
                             config_digest=args.config_digest, clock=clock)


def fresh_paths(paths, *, initial=False):
    # Initial author export may exist, but mutable source/tests/work must be empty.
    for name, value in paths.items():
        path = Path(value)
        for part in (path,*path.parents):
            if part.is_symlink():
                raise ValueError('operator_path_symlink')
        if initial and name=='author' and path.is_dir():
            for sub in ('src','tests','work'):
                if not (path/sub).is_dir() or any((path/sub).iterdir()):
                    raise ValueError('initial_author_not_fresh')
        elif path.exists():
            raise ValueError('epoch_path_not_fresh')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authority', required=True)
    sub = parser.add_subparsers(dest='operation', required=True)
    create = sub.add_parser('initialize')
    create.add_argument('--config', required=True);create.add_argument('--paths', required=True)
    create.add_argument('--author', required=True)
    create.add_argument('--identity-output', required=True)
    create.add_argument('--public-retention', action='store_true',
                        help='Bind the operator-only public retention gate at initialization')
    for name in ('serve','advance','recover','inspect','receipt'):
        p=sub.add_parser(name);p.add_argument('--authority-id',required=True);p.add_argument('--config-digest',required=True)
        if name in ('serve','advance','recover'):p.add_argument('--epoch',type=int,required=True)
        if name in ('advance','recover','receipt'):p.add_argument('--transition-id',required=True)
        if name=='advance':p.add_argument('--paths',required=True)
        if name=='recover':p.add_argument('--proof',required=True)
    args=parser.parse_args(argv)
    if args.operation=='initialize':
        config,paths=read(args.config),read(args.paths)
        inputs=fixed_inputs(config)
        if args.public_retention and 'retention' not in config:
            config=dict(config,retention=dict(schema='public-retention-v1',
                fixed_inputs_sha256=hashlib.sha256(canonical(inputs)).hexdigest(),repository_id=1431035719))
            if fixed_inputs(config) != inputs:
                raise ValueError('retention_fixed_inputs_changed')
        fresh_paths(paths,initial=True)
        database=Path(args.authority).absolute()
        if any(Path(p)==database or Path(p) in database.parents for p in paths.values()):
            raise ValueError('authority_must_be_outside_epoch_roots')
        bound={'trial':config,'inputs':inputs,'implementation':implementation(),
               'runtime_environment':runtime_environment()}
        authority=ResumableAuthority.create(database,profile=config['profile'],config=bound,
                    author=args.author,paths=paths,clock=clock)
        snap=authority.inspect()
        publish(args.identity_output,dict(authority_id=authority.authority_id,
            config_digest=authority.config_digest,epoch=0,author=args.author,
            original_deadline_monotonic=snap['run']['mono_deadline'],
            original_deadline_unix=snap['run']['wall_deadline'],
            scope='SQL initialization only; no native author or service started'))
        return 0
    authority=load(args)
    if args.operation=='inspect':
        print(json.dumps(authority.inspect(),sort_keys=True));return 0
    if args.operation=='receipt':
        print(json.dumps(authority.receipt(args.transition_id),sort_keys=True));return 0
    bound=json.loads(authority.inspect()['run']['config'])['config']
    if (bound['implementation'] != implementation() or bound['inputs'] != fixed_inputs(bound['trial']) or
            bound['runtime_environment'] != runtime_environment()):
        raise ValueError('pinned_implementation_or_inputs_changed')
    if args.operation=='advance':
        paths=read(args.paths);fresh_paths(paths)
        print(json.dumps(authority.advance(args.epoch,args.transition_id,paths),sort_keys=True));return 0
    if args.operation=='recover':
        print(json.dumps(recover_committed(authority,args.epoch,args.transition_id,read(args.proof)),sort_keys=True));return 0
    snap=authority.inspect();paths=paths_for(snap,args.epoch)
    # Construct ownership before probe, with guaranteed cleanup even on probe failure.
    trial=ResumableTrial(authority,args.epoch)
    try:
        trial.start_monitor()
        remaining=trial.remaining_seconds()
        if remaining <= 0 or not trial.current_epoch_active():
            raise AuthorityDenied('admission_closed')
        evidence=probe(bound['trial']['command'],trial.root/'adapter-probe.log',
                       timeout=min(30,remaining), deadline_monotonic=trial.deadline,
                       cancel_fd=trial.cancellation.read_fd)
        publish(trial.root/'launch.json',dict(profile=bound['trial']['profile'],authority_id=authority.authority_id,
            epoch=args.epoch,implementation=implementation(),probe=evidence,config_digest=authority.config_digest))
        if evidence['status']!='succeeded':
            authority.halt(args.epoch,identifier('probe_failed'),'probe_failed')
            return 1
        if trial.remaining_seconds() <= 0 or trial.stop_requested.is_set() or not trial.current_epoch_active():
            raise AuthorityDenied('admission_closed')
        run_service(trial)
        return 0
    finally:
        trial.close()


if __name__=='__main__':
    raise SystemExit(main())
