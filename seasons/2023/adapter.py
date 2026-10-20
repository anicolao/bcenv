#!/usr/bin/env python3
"""Versioned, offline BC23 builder tools. Not a sandbox for hostile bot sources."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime.artifacts import VERSION, digest, inventory, identity, write, execute, new_output

def package_name(value):
    if not re.fullmatch(r'[A-Za-z_]\w*(\.[A-Za-z_]\w*)*', value, flags=re.ASCII):
        raise ValueError('Invalid Java package name')
    return value


def runtime():
    keys = ['BCENV_ENGINE_JAR', 'BCENV_JAVA', 'BCENV_JAVAC']
    values = {key: os.environ.get(key) for key in keys}
    if not all(values.values()):
        raise ValueError('Use the pinned nix run .#bcenv wrapper; required runtime paths are missing')
    for path in values.values():
        if not Path(path).is_file():
            raise ValueError('Runtime file missing: ' + path)
    return values


def build(args):
    rt = runtime()
    package = package_name(args.package)
    source = Path(args.source).resolve()
    if not source.is_dir():
        raise ValueError('Source must be a directory')
    if Path(args.output).resolve().is_relative_to(source):
        raise ValueError('Build output must be outside the source tree')
    before = inventory(source)
    if not before or any(not p.endswith('.java') for p in before):
        raise ValueError('Source must contain only Java source files')
    if package.replace('.', '/') + '/RobotPlayer.java' not in before:
        raise ValueError('Missing package/RobotPlayer.java')
    out = new_output(args.output)
    shutil.copytree(source, out / 'source')
    if inventory(out / 'source') != before or inventory(source) != before:
        raise ValueError('Source changed while snapshotting')
    classes = out / 'classes'
    classes.mkdir()
    data = {'schema': VERSION, 'kind': 'build', 'season': '2023', 'package': package,
            'source_files': before, 'source_sha256': identity(before),
            'engine_sha256': digest(rt['BCENV_ENGINE_JAR']), 'steps': [], 'status': 'failed'}
    javac = [rt['BCENV_JAVAC'], '-source', '8', '-target', '8', '-encoding', 'UTF-8',
             '-proc:none', '-classpath', rt['BCENV_ENGINE_JAR'], '-d', str(classes)]
    # Source paths are absolute, so filenames cannot inject javac flags.
    javac += [str(out / 'source' / p) for p in before]
    data['steps'].append(execute(javac, out / 'compile.log', args.timeout))
    if data['steps'][-1]['status'] == 'succeeded':
        data['steps'].append(execute([rt['BCENV_JAVA'], '-cp', rt['BCENV_ENGINE_JAR'],
            'battlecode.instrumenter.Verifier', package, str(classes)], out / 'verify.log', args.timeout))
        if data['steps'][-1]['status'] == 'succeeded':
            data['status'] = 'verified'
    data['binary_files'] = inventory(classes)
    data['binary_sha256'] = identity(data['binary_files'])
    write(out / 'artifact.json', data)
    print(out / 'artifact.json')
    return 0 if data['status'] == 'verified' else 1


def load_build(path, engine):
    path = Path(path).resolve()
    data = json.loads((path / 'artifact.json').read_text())
    if data.get('schema') != VERSION or data.get('kind') != 'build' or data.get('status') != 'verified':
        raise ValueError('Requires a verified build artifact')
    if data['engine_sha256'] != digest(engine):
        raise ValueError('Engine differs from verified build')
    for folder, key in [('source', 'source'), ('classes', 'binary')]:
        files = inventory(path / folder)
        if files != data[key + '_files'] or identity(files) != data[key + '_sha256']:
            raise ValueError('Build artifact changed: ' + folder)
    package_name(data['package'])
    return path, data


def engine_version(path):
    """Read the exact released runtime's version, never infer it from a replay."""
    with zipfile.ZipFile(path) as engine:
        entry = engine.getinfo('battlecode_version')
        if entry.file_size > 64:
            raise ValueError('Invalid engine version metadata')
        version = engine.read(entry).decode('ascii').strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid engine version metadata')
    return version


def replay_matches(summary, map_name, team_a, team_b, spec_version):
    if not isinstance(summary, dict):
        return False
    try:
        matches = summary['matches']
        teams = {t['slot']: t['package'] for t in summary['teams']}
        return (summary['status'] == 'complete' and summary['spec_version'] == spec_version
                and len(matches) == 1 and matches[0]['map'] == map_name
                and len(summary['teams']) == 2 and teams == {'A': team_a, 'B': team_b}
                and summary['winner_slot'] in ('A', 'B')
                and matches[0]['winner_slot'] == summary['winner_slot'])
    except (KeyError, TypeError, IndexError):
        return False


def run(args):
    rt = runtime()
    version = engine_version(rt['BCENV_ENGINE_JAR'])
    a, ad = load_build(args.team_a, rt['BCENV_ENGINE_JAR'])
    b, bd = load_build(args.team_b, rt['BCENV_ENGINE_JAR'])
    if any(Path(args.output).resolve().is_relative_to(path) for path in (a, b)):
        raise ValueError('Match output must be outside input build artifacts')
    map_path = Path(args.map).resolve()
    if not map_path.is_file() or map_path.suffix != '.map23':
        raise ValueError('Provide an explicit .map23 file')
    out = new_output(args.output)
    for slot, path, data in [('A', a, ad), ('B', b, bd)]:
        shutil.copytree(path / 'classes', out / ('team-' + slot))
        if inventory(out / ('team-' + slot)) != data['binary_files']:
            raise ValueError('Binary changed while snapshotting')
    (out / 'maps').mkdir()
    shutil.copyfile(map_path, out / 'maps' / map_path.name)
    props = {'bc.server.mode': 'headless', 'bc.server.wait-for-client': 'false',
        'bc.server.websocket': 'false',
        'bc.game.map-path': str(out / 'maps'), 'bc.server.robot-player-to-system-out': 'false',
        'bc.server.debug': 'false', 'bc.engine.debug-methods': 'false',
        'bc.engine.enable-profiler': 'false', 'bc.engine.show-indicators': 'true',
        'bc.game.team-a': 'A', 'bc.game.team-b': 'B',
        'bc.game.team-a.url': str(out / 'team-A'), 'bc.game.team-b.url': str(out / 'team-B'),
        'bc.game.team-a.package': ad['package'], 'bc.game.team-b.package': bd['package'],
        'bc.game.maps': map_path.stem, 'bc.server.validate-maps': 'true',
        'bc.server.alternate-order': 'false', 'bc.server.save-file': str(out / 'replay.bc23')}
    cmd = [rt['BCENV_JAVA'], '-Xmx512m'] + ['-D' + k + '=' + v for k, v in props.items()]
    cmd += ['-cp', rt['BCENV_ENGINE_JAR'], 'battlecode.server.Main', '-c=-']
    result = {'schema': VERSION, 'kind': 'match', 'season': '2023',
        'engine_version': version,
        'engine_sha256': digest(rt['BCENV_ENGINE_JAR']), 'map_sha256': digest(out / 'maps' / map_path.name),
        'teams': {'A': {'source_sha256': ad['source_sha256'], 'binary_sha256': ad['binary_sha256'], 'package': ad['package']},
                  'B': {'source_sha256': bd['source_sha256'], 'binary_sha256': bd['binary_sha256'], 'package': bd['package']}},
        'config': props, 'process': execute(cmd, out / 'engine.log', args.timeout),
        'status': 'infrastructure_failure', 'winner': None}
    replay = out / 'replay.bc23'
    if replay.is_file():
        result['replay'] = {'path': replay.name, 'sha256': digest(replay)}
    if result['process']['status'] == 'timeout':
        result['status'] = 'timeout'
    elif result['process']['status'] == 'succeeded' and replay.is_file():
        result['status'] = 'replay_unvalidated'
        cp = os.environ.get('BCENV_REPLAY_CLASSPATH')
        if cp:
            result['extraction'] = execute([rt['BCENV_JAVA'], '-Xmx512m', '-cp', cp,
                'ReplaySummary', str(replay)], out / 'replay-summary.json', args.timeout,
                stderr=out / 'replay-extractor.log')
            if result['extraction']['status'] == 'succeeded':
                try:
                    summary = json.loads((out / 'replay-summary.json').read_text())
                except (ValueError, UnicodeError) as error:
                    summary = {}
                    result['extraction']['parse_error'] = str(error)
                if replay_matches(summary, map_path.stem, ad['package'], bd['package'], version):
                    result['status'] = 'completed'
                    result['winner'] = summary['winner_slot']
                    result['replay']['summary'] = 'replay-summary.json'
                    result['replay']['summary_sha256'] = digest(out / 'replay-summary.json')
    if (inventory(out / 'team-A') != ad['binary_files']
            or inventory(out / 'team-B') != bd['binary_files']
            or digest(out / 'maps' / map_path.name) != result['map_sha256']):
        result['status'] = 'input_mutated'
        result['winner'] = None
    write(out / 'result.json', result)
    print(out / 'result.json')
    return 0 if result['status'] == 'completed' else 1


def pack(args):
    rt = runtime()
    build_path, data = load_build(args.build, rt['BCENV_ENGINE_JAR'])
    if Path(args.output).resolve().is_relative_to(build_path):
        raise ValueError('Package output must be outside input build artifact')
    out = new_output(args.output)
    archive = out / 'source.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(data['source_files']):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (build_path / 'source' / name).read_bytes())
    write(out / 'package.json', {'schema': VERSION, 'kind': 'source-package',
          'source_sha256': data['source_sha256'], 'binary_sha256': data['binary_sha256'],
          'zip_sha256': digest(archive), 'package': data['package'], 'verified_engine_sha256': data['engine_sha256']})
    print(out / 'package.json')
    return 0


def replay_query(args):
    return _replay_query(args, paged=False)


def replay_page(args):
    return _replay_query(args, paged=True)


def _replay_query(args, *, paged):
    # Validate before allocating output or launching a process.
    if not 0 <= args.round_start <= args.round_end <= 2147483647:
        raise ValueError('round bounds require 0 <= start <= end <= 2147483647')
    if args.robot_id is not None and not 0 <= args.robot_id <= 2147483647:
        raise ValueError('robot ID must be between 0 and 2147483647')
    if not 1 <= args.limit <= 10000:
        raise ValueError('record limit must be between 1 and 10000')
    if paged:
        values = (args.round_start, args.round_end, args.limit, args.record_offset)
        if any(type(value) is not int for value in values):
            raise ValueError('page parameters must be integers')
        if args.robot_id is not None and type(args.robot_id) is not int:
            raise ValueError('page robot ID must be an integer')
        if args.round_end - args.round_start + 1 > 256:
            raise ValueError('page round span exceeds 256')
        if not 0 <= args.record_offset <= 5000000:
            raise ValueError('record offset must be between 0 and 5000000')
    replay = Path(args.replay).resolve()
    if not replay.is_file() or replay.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('replay must be a regular file no larger than 64 MiB')
    rt = runtime()
    cp = os.environ.get('BCENV_REPLAY_CLASSPATH')
    if not cp:
        raise ValueError('Pinned replay classes are missing')
    before = digest(replay)
    out = new_output(args.output)
    command = [rt['BCENV_JAVA'], '-Xmx512m', '-cp', cp, 'ReplayQuery']
    if paged:
        command.append('--page')
    command.extend([str(replay), str(args.round_start), str(args.round_end),
                    'all' if args.robot_id is None else str(args.robot_id), str(args.limit)])
    if paged:
        command.append(str(args.record_offset))
    process = execute(command, out / 'replay-query.json', args.timeout, stderr=out / 'replay-query.log')
    result = {'schema': VERSION, 'kind': 'replay-page' if paged else 'replay-query', 'season': '2023',
        'engine_sha256': digest(rt['BCENV_ENGINE_JAR']),
        'replay': {'path': str(replay), 'sha256': before}, 'process': process,
        'status': 'timeout' if process['status'] == 'timeout' else 'failed'}
    try:
        query = json.loads((out / 'replay-query.json').read_text())
        if (process['status'] == 'succeeded' and query.get('format') == ('bcenv.bc2023.replay-query.v2' if paged else 'bcenv.bc2023.replay-query.v1')
                and query.get('status') == 'complete' and query.get('source_replay', {}).get('sha256') == before
                and (not paged or _page_receipt_matches(query, args))):
            result['status'] = 'completed'
            result['truncated'] = query['truncated']
    except (ValueError, UnicodeError, AttributeError) as error:
        result['parse_error'] = str(error)
    if not replay.is_file() or digest(replay) != before:
        result['status'] = 'input_mutated'
    write(out / 'query-result.json', result)
    print(out / 'query-result.json')
    return 0 if result['status'] == 'completed' else 1



def _page_receipt_matches(query, args):
    # This artifact receipt is not the sanitizer or an authenticity/ownership check.
    selected = query.get('query', {})
    for key, expected in (('round_start', args.round_start), ('round_end', args.round_end),
                          ('robot_id', args.robot_id), ('record_limit', args.limit),
                          ('record_offset', args.record_offset)):
        actual = selected.get(key)
        if key not in selected or type(actual) is not type(expected) or actual != expected:
            return False
    count, returned = query.get('matching_record_count'), query.get('returned_record_count')
    if (type(count) is not int or not args.record_offset <= count <= 5000000 or
            type(returned) is not int or returned != min(count - args.record_offset, args.limit)):
        return False
    end = args.record_offset + returned
    expected_next = end if end < count else None
    return (query.get('source_stream_complete') is True and
            query.get('extracted_fields_valid') is True and
            query.get('all_schema_fields_validated') is False and
            query.get('page_records_complete') is True and
            type(query.get('page_end_offset')) is int and query['page_end_offset'] == end and
            'next_record_offset' in query and
            type(query['next_record_offset']) is type(expected_next) and
            query['next_record_offset'] == expected_next and
            query.get('truncated') is (returned < count) and
            query.get('selected_records_complete') is (returned == count) and
            type(query.get('records')) is list and len(query['records']) == returned)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('build'); p.add_argument('--source', required=True); p.add_argument('--package', required=True)
    p.set_defaults(fn=build)
    p = sub.add_parser('run'); p.add_argument('--team-a', required=True); p.add_argument('--team-b', required=True)
    p.add_argument('--map', required=True); p.set_defaults(fn=run)
    p = sub.add_parser('package'); p.add_argument('--build', required=True); p.set_defaults(fn=pack)
    for p in sub.choices.values():
        p.add_argument('--output', required=True)
        p.add_argument('--timeout', type=int, default=120)
    p = sub.add_parser('replay')
    replay_sub = p.add_subparsers(dest='replay_command', required=True)
    p = replay_sub.add_parser('query')
    p.add_argument('--replay', required=True)
    p.add_argument('--round-start', required=True, type=int)
    p.add_argument('--round-end', required=True, type=int)
    p.add_argument('--robot-id', type=int)
    p.add_argument('--limit', type=int, default=1000)
    p.add_argument('--output', required=True)
    p.add_argument('--timeout', type=int, default=120)
    p.set_defaults(fn=replay_query)
    p = replay_sub.add_parser('page')
    p.add_argument('--replay', required=True)
    p.add_argument('--round-start', required=True, type=int)
    p.add_argument('--round-end', required=True, type=int)
    p.add_argument('--robot-id', type=int)
    p.add_argument('--record-offset', required=True, type=int)
    p.add_argument('--limit', type=int, default=64)
    p.add_argument('--output', required=True)
    p.add_argument('--timeout', type=int, default=120)
    p.set_defaults(fn=replay_page)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error('timeout must be between 1 and 600 seconds')
    try:
        return args.fn(args)
    except (ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
