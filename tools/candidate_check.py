#!/usr/bin/env python3
"""Cooperative local Java checks. Not a sandbox or an engine-semantics oracle."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.artifacts import digest, inventory, identity, new_output, write

spec = importlib.util.spec_from_file_location('candidate_check_adapter', ROOT / 'seasons/2023/adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
MAX_LOG = 65536


def execute(command, output, timeout, stderr=None):
    """Drain bounded combined output; kill this process group on timeout/overflow."""
    if stderr is not None:
        raise ValueError('Separate stderr is unsupported')
    start = time.monotonic()
    command = list(command)
    # This runner is used only for pinned Java compiler/runtime commands.
    command.insert(1, '-J-Xmx256m' if Path(command[0]).name == 'javac' else '-Xmx256m')
    result = {'command': command, 'exit_code': None, 'status': 'failed', 'log': Path(output).name}
    with Path(output).open('xb') as log:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True, stdin=subprocess.DEVNULL, cwd=Path(output).resolve().parent,
                                       env={k: v for k, v in os.environ.items()
                                            if k not in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'CLASSPATH')})
        except OSError as error:
            result['launch_error'] = str(error)
        else:
            count = 0
            status = None
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        if time.monotonic() - start >= timeout:
                            status = 'timeout'
                            break
                        for key, _ in selector.select(min(.05, timeout)):
                            chunk = os.read(key.fileobj.fileno(), 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            log.write(chunk[:max(0, MAX_LOG - count)])
                            count += len(chunk)
                            if count > MAX_LOG:
                                status = 'output_limit'
                                break
                        if status:
                            break
                    if status is None:
                        remaining = max(.001, timeout - (time.monotonic() - start))
                        try:
                            process.wait(timeout=remaining)
                        except subprocess.TimeoutExpired:
                            status = 'timeout'
            finally:
                # Also clean up cooperative children that retain inherited pipes.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                process.stdout.close()
            result['exit_code'] = process.returncode
            result['status'] = status or ('succeeded' if process.returncode == 0 else 'failed')
    result.update(elapsed_seconds=round(time.monotonic() - start, 3), log_sha256=digest(output))
    return result


def build(args):
    # Private module instance: reuse the official build/snapshot/verifier algorithm,
    # replacing only execution with bounded logs and heap. Strict adapter unchanged.
    adapter.execute = execute
    with contextlib.redirect_stdout(io.StringIO()):
        code = adapter.build(args)
    out = Path(args.output).resolve()
    artifact = json.loads((out / 'artifact.json').read_text())
    steps = artifact['steps']
    summary = {'schema': 'bcenv.candidate-check.v1', 'kind': 'build-check',
               'status': artifact['status'], 'source_sha256': artifact['source_sha256'],
               'binary_sha256': artifact['binary_sha256'],
               'compile_status': steps[0]['status'] if steps else 'not_run',
               'official_verifier_status': steps[1]['status'] if len(steps) > 1 else 'not_run',
               'artifact': str(out / 'artifact.json'), 'artifact_sha256': digest(out / 'artifact.json')}
    write(out / 'check.json', summary)
    return summary, code


def test(args):
    rt = adapter.runtime()
    build_path, built = adapter.load_build(args.build, rt['BCENV_ENGINE_JAR'])
    tests = Path(args.tests).resolve()
    if not tests.is_dir():
        raise ValueError('Tests must be a directory of Java sources')
    before = inventory(tests)
    if not before or any(not p.endswith('.java') for p in before):
        raise ValueError('Tests must contain only Java sources')
    main_class = adapter.package_name(args.main_class)
    target = Path(args.output).resolve()
    if target.is_relative_to(tests) or target.is_relative_to(build_path):
        raise ValueError('Output must be outside input trees')
    out = new_output(target)
    shutil.copytree(tests, out / 'test-source')
    shutil.copytree(build_path / 'classes', out / 'candidate-classes')
    if inventory(tests) != before or inventory(out / 'test-source') != before:
        raise ValueError('Tests changed while snapshotting')
    if inventory(out / 'candidate-classes') != built['binary_files']:
        raise ValueError('Candidate changed while snapshotting')
    classes = out / 'test-classes'
    classes.mkdir()
    cp = os.pathsep.join([str(out / 'candidate-classes'), rt['BCENV_ENGINE_JAR']])
    compile_step = execute([rt['BCENV_JAVAC'], '-source', '8', '-target', '8', '-encoding', 'UTF-8',
                            '-proc:none', '-classpath', cp, '-d', str(classes)] +
                           [str(out / 'test-source' / p) for p in before], out / 'compile.log', args.timeout)
    result = {'schema': 'bcenv.candidate-check.v1', 'kind': 'java-test', 'status': 'failed',
              'source_sha256': built['source_sha256'], 'binary_sha256': built['binary_sha256'],
              'engine_sha256': built['engine_sha256'], 'test_files': before, 'test_sha256': identity(before),
              'main_class': main_class, 'steps': [compile_step], 'semantics': 'uninstrumented-jvm'}
    if compile_step['status'] == 'succeeded':
        compiled = inventory(classes)
        result['test_binary_files'] = compiled
        result['test_binary_sha256'] = identity(compiled)
        with zipfile.ZipFile(rt['BCENV_ENGINE_JAR']) as engine:
            engine_classes = {name for name in engine.namelist() if name.endswith('.class')}
        if set(compiled) & engine_classes:
            result['reason'] = 'test_classes_shadow_public_api'
        elif set(compiled) & set(built['binary_files']):
            result['reason'] = 'test_classes_shadow_candidate'
        elif main_class.replace('.', '/') + '.class' not in compiled:
            result['reason'] = 'main_class_not_in_test_sources'
        else:
            run_step = execute([rt['BCENV_JAVA'], '-ea', '-cp', cp + os.pathsep + str(classes), main_class],
                               out / 'test.log', args.timeout)
            result['steps'].append(run_step)
            if run_step['status'] == 'succeeded':
                result['status'] = 'passed'
    # Detect accidental test mutation of its own snapshots; not hostile tamper proof.
    if (inventory(out / 'candidate-classes') != built['binary_files'] or
            inventory(out / 'test-source') != before or
            ('test_binary_files' in result and inventory(classes) != result['test_binary_files'])):
        result.update(status='failed', reason='snapshot_changed')
    try:
        adapter.load_build(build_path, rt['BCENV_ENGINE_JAR'])
    except (ValueError, OSError, KeyError):
        result.update(status='failed', reason='input_build_changed')
    write(out / 'check.json', result)
    return result, 0 if result['status'] == 'passed' else 1


def main(argv=None, workspace=None):
    parser = argparse.ArgumentParser(description=__doc__)
    if workspace is None:
        parser.add_argument('--workspace', required=True, help='All input/output paths must stay within this candidate root')
    else:
        parser.set_defaults(workspace=workspace)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('build')
    p.add_argument('--source', required=True)
    p.add_argument('--package', required=True)
    p.set_defaults(fn=build)
    p = sub.add_parser('test')
    p.add_argument('--build', required=True)
    p.add_argument('--tests', required=True)
    p.add_argument('--main-class', required=True)
    p.set_defaults(fn=test)
    for p in sub.choices.values():
        p.add_argument('--output', required=True)
        p.add_argument('--timeout', type=int, default=30)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 120:
        parser.error('timeout must be 1..120 seconds per phase')
    try:
        workspace = Path(args.workspace).resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError('Workspace must be a directory')
        for name in ('source', 'build', 'tests', 'output'):
            if hasattr(args, name) and not Path(getattr(args, name)).resolve().is_relative_to(workspace):
                raise ValueError(name + ' must stay within candidate workspace')
        result, code = args.fn(args)
    except (ValueError, OSError, KeyError) as error:
        result, code = {'schema': 'bcenv.candidate-check.v1', 'status': 'error', 'error': str(error)}, 2
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
