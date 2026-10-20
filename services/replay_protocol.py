"""Closed opt-in v2 replay tool schema and second public-output projection.

No source bytes, source digest, filesystem path or provider error is public.
The v1 registry and withholding behavior remain the default in the controller.
"""
from dataclasses import dataclass
import json
import re

from services import replay_feedback as v1

MAX_PAYLOAD_BYTES = 12288
SCHEMA = 'bcenv.replay-route.v2'
_ID = {kind: re.compile(kind + r'_[0-9a-f]{32}\Z') for kind in ('s', 'j', 'r', 'v')}
REASONS = frozenset(('not_found', 'not_ready', 'replay_not_available', 'resource_limit',
                     'invalid_evidence', 'replay_conflict', 'stale_reservation', 'rate_limit',
                     'clock_regression', 'configuration_mismatch', 'storage_unavailable', 'storage_integrity'))
_FIELDS = ('submission_id', 'operation', 'replay_id', 'view_id', 'manifest_offset', 'manifest_limit',
           'round_start', 'round_end', 'robot_id', 'record_limit', 'record_offset')


@dataclass(frozen=True)
class ReplayRequest:
    submission_id: str
    operation: str
    replay_id: str | None
    view_id: str | None
    manifest_offset: int | None
    manifest_limit: int | None
    round_start: int | None
    round_end: int | None
    robot_id: int | None
    record_limit: int | None
    record_offset: int | None


@dataclass(frozen=True)
class ReplayReply:
    payload: bytes


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _need(condition):
    if not condition:
        raise ValueError('invalid_replay_protocol')


def _id(value, kind):
    _need(type(value) is str and _ID[kind].fullmatch(value) is not None)
    return value


def _int(value, low=0, high=2147483647):
    _need(type(value) is int and low <= value <= high)
    return value


def _keys(value, names):
    _need(type(value) is dict and set(value) == set(names))


def replay_fields():
    """Flat strict schema: every field required; operation-specific fields nullable."""
    def integer(low, high):
        return {'type': ['integer', 'null'], 'minimum': low, 'maximum': high}
    return {'submission_id': {'type': 'string', 'pattern': '^s_[0-9a-f]{32}$'},
            'operation': {'type': 'string', 'enum': ['manifest', 'window', 'page']},
            'replay_id': {'type': ['string', 'null'], 'pattern': '^r_[0-9a-f]{32}$'},
            'view_id': {'type': ['string', 'null'], 'pattern': '^v_[0-9a-f]{32}$'},
            'manifest_offset': integer(0, 8), 'manifest_limit': integer(1, 8),
            'round_start': integer(0, 2147483647), 'round_end': integer(0, 2147483647),
            'robot_id': integer(0, 2147483647), 'record_limit': integer(1, 64),
            'record_offset': integer(0, 5000000)}


def parse_request(arguments):
    _keys(arguments, _FIELDS)
    _id(arguments['submission_id'], 's')
    op = arguments['operation']
    _need(type(op) is str and op in ('manifest', 'window', 'page'))
    active = {'submission_id', 'operation'}
    if op == 'manifest':
        active |= {'manifest_offset', 'manifest_limit'}
        _int(arguments['manifest_offset'], 0, 8)
        _int(arguments['manifest_limit'], 1, 8)
    elif op == 'window':
        active |= {'replay_id', 'round_start', 'round_end', 'robot_id', 'record_limit'}
        _id(arguments['replay_id'], 'r')
        start, end = _int(arguments['round_start']), _int(arguments['round_end'])
        _need(0 <= end - start < 256)
        if arguments['robot_id'] is not None:
            _int(arguments['robot_id'])
        _int(arguments['record_limit'], 1, 64)
    else:
        active |= {'view_id', 'record_offset'}
        _id(arguments['view_id'], 'v')
        _int(arguments['record_offset'], 0, 5000000)
    _need(all(arguments[key] is None for key in set(_FIELDS) - active))
    return ReplayRequest(**arguments)


def unavailable(submission_id, reason):
    _id(submission_id, 's')
    return encoded({'schema': SCHEMA, 'submission_id': submission_id, 'status': 'unavailable',
                    'reason': reason if type(reason) is str and reason in REASONS else 'invalid_evidence'})


def _page(value, request):
    _keys(value, ('schema','status','spec_version','submission_id','job_id','own_team_id','query','matches','records','coverage','limits'))
    _need(value['schema'] == 'bcenv.bc2023.replay-feedback.v2' and value['status'] == 'available')
    _need(value['spec_version'] in ('2.0.3', '3.0.13') and value['submission_id'] == request.submission_id)
    _id(value['job_id'], 'j')
    _int(value['own_team_id'], 1, 2)
    q = value['query']
    _keys(q, ('round_start','round_end','robot_id','record_offset','source_record_limit',
              'round_bounds_inclusive','team_resource_rows_included_with_robot_filter','match_scope'))
    _int(q['round_start']); _int(q['round_end']); _int(q['record_offset'], 0, 5000000)
    _int(q['source_record_limit'], 1, 64)
    _need(0 <= q['round_end'] - q['round_start'] < 256)
    _need(q['round_bounds_inclusive'] is True and q['team_resource_rows_included_with_robot_filter'] is True and q['match_scope'] == 'all_matches')
    if q['robot_id'] is not None:
        _int(q['robot_id'])
    if request.operation == 'window':
        _need(q['record_offset'] == 0 and all(q[key] == getattr(request, key) for key in ('round_start','round_end','robot_id')) and
              q['source_record_limit'] == request.record_limit)
    else:
        _need(q['record_offset'] == request.record_offset)
    _need(type(value['matches']) is list and 1 <= len(value['matches']) <= 8)
    matches = [v1._match(row, index, q) for index, row in enumerate(value['matches'])]
    _need(encoded(matches) == encoded(value['matches']))
    _need(type(value['records']) is list and len(value['records']) <= 64)
    records = [v1._record(row, matches, q, value['own_team_id']) for row in value['records']]
    _need(encoded(records) == encoded(value['records']))
    coverage = value['coverage']
    _keys(coverage, ('source_stream_complete','extracted_fields_valid','all_schema_fields_validated',
        'matching_record_count','matching_counts_by_kind','source_returned_record_count','returned_record_count',
        'source_page_records_complete','source_page_end_offset','source_next_record_offset','output_page_end_offset',
        'next_record_offset','has_more','supplied_page_records_complete','selected_records_complete','truncated','truncation_reasons'))
    for key in ('source_stream_complete','extracted_fields_valid','source_page_records_complete'):
        _need(coverage[key] is True)
    _need(coverage['all_schema_fields_validated'] is False)
    for key in ('has_more','supplied_page_records_complete','selected_records_complete','truncated'):
        _need(type(coverage[key]) is bool)
    for key in ('matching_record_count','source_returned_record_count','returned_record_count','source_page_end_offset','output_page_end_offset'):
        _int(coverage[key], 0, 5000000)
    for key in ('source_next_record_offset', 'next_record_offset'):
        if coverage[key] is not None:
            _int(coverage[key], 0, 5000000)
    counts = coverage['matching_counts_by_kind']
    _need(type(counts) is dict and set(counts) <= set(v1.KINDS))
    for count in counts.values():
        _int(count, 0, 5000000)
    _need(sum(counts.values()) == coverage['matching_record_count'])
    end = q['record_offset'] + len(records)
    _need(coverage['returned_record_count'] == len(records) and coverage['output_page_end_offset'] == end and
          end <= coverage['matching_record_count'])
    more = end < coverage['matching_record_count']
    _need(coverage['has_more'] is more and coverage['next_record_offset'] == (end if more else None) and (not more or bool(records)))
    reasons = coverage['truncation_reasons']
    _need(type(reasons) is list and len(reasons) <= 4 and all(type(reason) is str and reason in
        ('source_record_offset','source_record_limit','projection_record_limit','projection_output_limit') for reason in reasons))
    limits = value['limits']
    _keys(limits, ('max_records','max_round_span','max_output_encoded_bytes'))
    _int(limits['max_records'], 1, 64); _int(limits['max_round_span'], 1, 256)
    _int(limits['max_output_encoded_bytes'], 1, MAX_PAYLOAD_BYTES)
    source_count = coverage['source_returned_record_count']
    source_end = q['record_offset'] + source_count
    source_next = source_end if source_end < coverage['matching_record_count'] else None
    _need(len(records) <= min(source_count, limits['max_records']) and
          source_count == min(coverage['matching_record_count'] - q['record_offset'], q['source_record_limit']) and
          coverage['source_page_end_offset'] == source_end and coverage['source_next_record_offset'] == source_next and
          coverage['supplied_page_records_complete'] is (len(records) == source_count) and
          coverage['selected_records_complete'] is (len(records) == coverage['matching_record_count']) and
          coverage['truncated'] is (len(records) < coverage['matching_record_count']))
    expected_reasons = []
    if q['record_offset']:
        expected_reasons.append('source_record_offset')
    if source_next is not None:
        expected_reasons.append('source_record_limit')
    if source_count > limits['max_records']:
        expected_reasons.append('projection_record_limit')
    if len(records) < min(source_count, limits['max_records']):
        expected_reasons.append('projection_output_limit')
    _need(reasons == expected_reasons)
    _need(all(sum(row['kind'] == kind for row in records) <= counts.get(kind, 0) for kind in v1.KINDS))
    return value


def _decode_envelope(data):
    # Route expiry follows the ledger clock domain, wider than signed game ints.
    # Byte/depth/token bounds apply before allocation/conversion; page field
    # validators below still enforce the narrower game-specific integer ranges.
    def integer(token):
        _need(len(token) <= 20)
        value = int(token)
        _need(-(2**61) <= value <= 2**61)
        return value
    def invalid_number(token):
        raise ValueError('invalid_replay_protocol')
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result)
            result[key] = value
        return result
    text = data.decode('utf-8')
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            _need(depth <= 32)
        elif char in ']}':
            depth -= 1
    value = json.loads(text, object_pairs_hook=object_pairs, parse_int=integer,
                       parse_float=invalid_number, parse_constant=invalid_number)
    _need(type(value) is dict)
    return value


def project_reply(reply, request):
    """Validate again at controller boundary, including strict UTF-8/byte cap.

    Even an arbitrary adapter cannot leak extra fields or strings through this
    route. Schema validity still does not establish real replay authenticity.
    """
    _need(type(reply) is ReplayReply and type(reply.payload) is bytes and len(reply.payload) <= MAX_PAYLOAD_BYTES)
    _need(type(request) is ReplayRequest)
    value = _decode_envelope(reply.payload)
    _need(value.get('schema') == SCHEMA and value.get('submission_id') == request.submission_id)
    if value.get('status') == 'unavailable':
        _keys(value, ('schema','submission_id','status','reason'))
        _need(type(value['reason']) is str and value['reason'] in REASONS)
    elif request.operation == 'manifest':
        _keys(value, ('schema','submission_id','status','operation','replays','total_replays','next_manifest_offset'))
        _need(value['status'] == 'available' and value['operation'] == 'manifest')
        _int(value['total_replays'], 1, 8)
        _need(type(value['replays']) is list and len(value['replays']) <= request.manifest_limit)
        for row in value['replays']:
            _keys(row, ('replay_id','own_team_id','match_count','max_reported_rounds','expires_at','spec_version'))
            _id(row['replay_id'], 'r'); _int(row['own_team_id'], 1, 2); _int(row['match_count'], 1, 8)
            _int(row['max_reported_rounds']); _int(row['expires_at'], 0, 2**61)
            _need(row['spec_version'] in ('2.0.3', '3.0.13'))
        if value['next_manifest_offset'] is not None:
            _int(value['next_manifest_offset'], 0, 8)
        _need(len({row['replay_id'] for row in value['replays']}) == len(value['replays']))
        _need(len(value['replays']) == min(value['total_replays'] - request.manifest_offset, request.manifest_limit))
        end = request.manifest_offset + len(value['replays'])
        _need(end <= value['total_replays'] and value['next_manifest_offset'] == (end if end < value['total_replays'] else None))
        _need(value['next_manifest_offset'] is None or bool(value['replays']))
    else:
        _keys(value, ('schema','submission_id','status','operation','replay_id','view_id','page'))
        _need(value['status'] == 'available' and value['operation'] == request.operation)
        _id(value['replay_id'], 'r'); _id(value['view_id'], 'v')
        if request.operation == 'window':
            _need(value['replay_id'] == request.replay_id)
        else:
            _need(value['view_id'] == request.view_id)
        _page(value['page'], request)
    _need(len(encoded(value)) <= MAX_PAYLOAD_BYTES)
    return value
