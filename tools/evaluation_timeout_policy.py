"""Prospective evaluator-only timeout policy; never a candidate/service endpoint.

This consumes the existing pinned adapter contract, not inferred process death.
It neither schedules nor retries work. Only successful durable publication returns
eligibility for the next untouched assignment under the original admission rules.
Filesystem cooperation is assumed; there is no hostile-writer or descendant claim.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

SCHEMA = 'bcenv.evaluation-timeout-censor.v1'
ACK = {'schema': 'bcenv.cooperative-cleanup.v1', 'direct_child_waits_completed': True,
       'scope': 'normal adapter return only; not descendant containment'}
EXPECTED_KEYS = {'assignment_id','index','debit_id','protocol_sha256','runtime_identity',
                 'command','input_identity','per_match_seconds','global_deadline_monotonic'}
EVIDENCE_KEYS = {'assignment_id','index','debit_id','runtime_identity','command','result_bytes','ack_bytes',
                 'outer_wait_completed','outer_returncode','outer_wait_monotonic',
                 'ownership_unambiguous','artifacts_validated','other_infrastructure_failure'}
MAX_RECORD = 65536


class CensoringRefused(ValueError):
    """Stop; this observation cannot authorize continued evaluation."""


class CensoringCommitUnconfirmed(RuntimeError):
    """Stop and preserve artifacts; a record may already exist. Never retry."""


def require(condition, reason):
    if not condition:
        raise CensoringRefused(reason)


def canonical(value):
    try:
        data=json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=True,allow_nan=False).encode()
    except (ValueError,TypeError,RecursionError):
        raise CensoringRefused('invalid canonical evidence') from None
    require(len(data)<=MAX_RECORD,'record bound')
    return data


def number(value):
    return type(value) in (int,float) and math.isfinite(value)


def _pairs(values):
    result={}
    for key,value in values:
        require(key not in result,'duplicate JSON key')
        result[key]=value
    return result


def _read(raw, limit):
    require(type(raw) is bytes and 0<len(raw)<=limit,'artifact bound/type')
    try:
        value=json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda x: (_ for _ in ()).throw(CensoringRefused('nonfinite JSON')))
    except (ValueError,UnicodeError,RecursionError):
        raise CensoringRefused('invalid artifact JSON') from None
    require(type(value) is dict,'artifact object required')
    return value


def _option(command, key):
    require(command.count(key)==1 and not any(s.startswith(key+'=') for s in command),'exact command option '+key)
    index=command.index(key)
    require(index+1<len(command),'missing command value')
    return command[index+1]


def validate_expected(expected, *, now_monotonic):
    """Shared bounded preflight; does not prove trusted pins or admit any work."""
    require(type(expected) is dict and set(expected)==EXPECTED_KEYS,'expected schema')
    expected=json.loads(canonical(expected))
    for key in ('assignment_id','debit_id'):
        require(type(expected[key]) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',expected[key]) is not None,'identity format')
    require(type(expected['index']) is int and 0<=expected['index']<4096,'assignment index')
    require(type(expected['protocol_sha256']) is str and re.fullmatch('[0-9a-f]{64}',expected['protocol_sha256']) is not None,'protocol identity')
    require(type(expected['runtime_identity']) is dict and bool(expected['runtime_identity']),'runtime identity')
    cap=expected['per_match_seconds'];global_deadline=expected['global_deadline_monotonic']
    require(type(cap) is int and 1<=cap<=600,'original adapter cap')
    require(number(global_deadline) and number(now_monotonic) and now_monotonic<global_deadline,'original global budget exhausted/unknown')
    command=expected['command']
    require(type(command) is list and 2<=len(command)<=128 and all(type(v) is str and 0<len(v)<=4096 for v in command),'command schema')
    require(command.count('run')==1 and not any(v=='--cancel-fd' or v.startswith('--cancel-fd=') for v in command),'not uncancelled engine invocation')
    arguments=command[command.index('run')+1:]
    options={'--team-a','--team-b','--map','--output','--timeout','--deadline-monotonic','--cleanup-ack'}
    require(len(arguments)==2*len(options) and set(arguments[::2])==options,'exact adapter options; no abbreviation/override')
    require(_option(command,'--timeout')==str(cap),'cap changed')
    try:
        passed_deadline=float(_option(command,'--deadline-monotonic'))
    except ValueError:
        raise CensoringRefused('invalid command deadline') from None
    require(passed_deadline==global_deadline,'original deadline changed')
    require(bool(_option(command,'--cleanup-ack')) and bool(_option(command,'--output')),'invocation artifact paths absent')
    identity=expected['input_identity']
    require(type(identity) is dict and set(identity)=={'engine_version','engine_sha256','map_sha256','teams'},'input identity schema')
    require(type(identity['engine_version']) is str and 0<len(identity['engine_version'])<=128,'engine version')
    hashes=[identity['engine_sha256'],identity['map_sha256']]
    teams=identity['teams']
    require(type(teams) is dict and set(teams)=={'A','B'},'team identity schema')
    for team in teams.values():
        require(type(team) is dict and set(team)=={'source_sha256','binary_sha256','package'} and
                type(team['package']) is str and 0<len(team['package'])<=256,'team identity schema')
        hashes.extend((team['source_sha256'],team['binary_sha256']))
    require(all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None for v in hashes),'input digest schema')
    return expected


def classify_timeout(expected, evidence, *, now_monotonic):
    """Validate positive retained evidence; returned record alone is NOT permission.

    ``expected`` is fixed before execution. ``evidence`` is supplied only by the
    trusted evaluator after actual outer wait and bounded artifact reads. Boolean
    attestations are not authentication. Missing diagnostic samples are irrelevant.
    artifacts_validated must include exact pinned Java/heap/engine/config command
    semantics and immutable input/file validation, not just a status/schema check.
    This module compares supplied identities, not external runtime installation.
    """
    expected=validate_expected(expected,now_monotonic=now_monotonic)
    require(type(evidence) is dict and set(evidence)==EVIDENCE_KEYS,'evidence schema')
    for key in ('assignment_id','index','debit_id','runtime_identity','command'):
        require(type(evidence[key]) is type(expected[key]) and canonical(evidence[key])==canonical(expected[key]),'identity mismatch '+key)
    cap=expected['per_match_seconds'];global_deadline=expected['global_deadline_monotonic']
    require(evidence['outer_wait_completed'] is True and evidence['ownership_unambiguous'] is True and
            evidence['artifacts_validated'] is True and evidence['other_infrastructure_failure'] is False,'ownership/artifact/transport uncertainty')
    require(type(evidence['outer_returncode']) is int and evidence['outer_returncode']==1,'outer adapter did not return timeout contract')
    outer_wait=evidence['outer_wait_monotonic']
    require(number(outer_wait) and outer_wait<=now_monotonic,'outer wait clock')
    raw=evidence['result_bytes'];ack_raw=evidence['ack_bytes']
    result=_read(raw,1048576);ack=_read(ack_raw,4096)
    require(ack==ACK and ack.get('direct_child_waits_completed') is True,'adapter cleanup acknowledgment')
    require(result.get('schema')=='bcenv.artifact.v1' and result.get('kind')=='match' and result.get('season')=='2023' and
            result.get('status')=='timeout' and 'winner' in result and result['winner'] is None and 'extraction' not in result,'not engine execution timeout')
    identity=expected['input_identity']
    require({key:result.get(key) for key in identity}==identity,'input identity mismatch')
    process=result.get('process')
    require(type(process) is dict and process.get('status')=='timeout' and process.get('child_reaped') is True and
            'not_started' not in process and 'launch_error' not in process and type(process.get('exit_code')) is int,'no positive inner wait')
    require(type(process.get('command')) is list and bool(process['command']) and
            all(type(v) is str and 0<len(v)<=4096 for v in process['command']),'missing engine command evidence')
    require(number(process.get('deadline_monotonic')) and process['deadline_monotonic']==global_deadline,'inner deadline differs')
    times=[process.get(k) for k in ('launch_checked_monotonic','termination_requested_monotonic','reaped_monotonic','finished_monotonic')]
    require(all(number(v) for v in times),'missing inner lifecycle clock')
    launch,termination,reaped,finished=times
    require(launch<=termination<=reaped<=finished<=outer_wait<=now_monotonic<global_deadline,'contradictory/clipped lifecycle')
    elapsed=process.get('elapsed_seconds')
    require(number(elapsed) and elapsed>=cap,'elapsed/cap inconsistency')
    # Rounded elapsed is a consistency check, not an exact timer-trigger field.
    # Under the pinned no-cancel runtime contract, a global-expiry timeout cannot
    # finish/reap before that unchanged global deadline. No old missing wait data
    # are manufactured or upgraded by this predicate.
    record=dict(schema=SCHEMA,status='timeout_censored',scored=False,retry_allowed=False,
        replacement_allowed=False,expected=expected,reason='engine_execution_per_match_timeout',
        result_sha256=hashlib.sha256(raw).hexdigest(),ack_sha256=hashlib.sha256(ack_raw).hexdigest(),
        lifecycle=dict(launch_checked_monotonic=launch,termination_requested_monotonic=termination,
            reaped_monotonic=reaped,finished_monotonic=finished,inner_exit_code=process['exit_code'],
            outer_wait_monotonic=outer_wait,outer_returncode=evidence['outer_returncode'],
            observed_monotonic=now_monotonic,elapsed_seconds=elapsed),
        qualification='pinned no-cancel adapter contract and positive direct-child wait chain; not exact timer instrumentation or arbitrary-descendant containment')
    canonical(record)
    return record


def publish_censored(path, expected, evidence, *, clock=time.monotonic):
    """Exclusive, fsynced publication; any failure is fail-stop, never a retry.

    Parent directory must already exist. Atomic link refuses replacement even for
    identical records. Partial/committed evidence is preserved on uncertainty.
    Fresh remaining global budget is checked after durable commit/readback; caller
    must check it again at the next untouched assignment's original admission.
    """
    record=classify_timeout(expected,evidence,now_monotonic=clock())
    expected=record['expected']  # Freeze identity/deadline through publication.
    data=canonical(record);path=Path(path)
    require(path.name not in ('','.', '..'),'record path')
    temporary='.'+path.name+'.'+uuid.uuid4().hex+'.pending'
    directory=None;fd=None
    try:
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        with os.fdopen(fd,'wb') as handle:
            fd=None
            require(handle.write(data)==len(data),'short record write')
            handle.flush();os.fsync(handle.fileno())
        os.link(temporary,path.name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
        os.fsync(directory)
        check=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
        with os.fdopen(check,'rb') as handle:
            require(handle.read(MAX_RECORD+1)==data,'committed record readback differs')
        os.unlink(temporary,dir_fd=directory);os.fsync(directory)
        after=clock()
        require(number(after) and record['lifecycle']['observed_monotonic']<=after<expected['global_deadline_monotonic'],'post-commit clock/budget invalid')
        return dict(schema=SCHEMA,status='censored_commit_confirmed',assignment_id=expected['assignment_id'],
            index=expected['index'],debit_id=expected['debit_id'],record_path=str(path.absolute()),record_sha256=hashlib.sha256(data).hexdigest(),
            eligible_next_untouched=True,global_deadline_monotonic=expected['global_deadline_monotonic'],
            committed_checked_monotonic=after)
    except BaseException as error:
        raise CensoringCommitUnconfirmed('censor persistence/remaining budget unconfirmed; stop, preserve, no retry') from error
    finally:
        if fd is not None:os.close(fd)
        if directory is not None:os.close(directory)
