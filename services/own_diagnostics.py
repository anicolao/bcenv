"""Bounded own-team diagnostic projection from a trusted replay extractor.

No IO, authorization, or ownership discovery occurs here. The host must bind the
immutable replay and own-team slot and invoke OwnDiagnostics, which resolves
ownership and filters BEFORE pagination/counting. Shape checks cannot prove that
untrusted JSON was produced by that extractor. Never expose raw engine logs.
"""
from services import replay_feedback as safe
from services.replay_feedback import FeedbackLimits, FeedbackRejected
from services.replay_feedback_v2 import ReplayPageQuery

SCHEMA = 'bcenv.bc2023.own-diagnostics.v1'
QUERY_FORMAT = 'bcenv.bc2023.own-diagnostics-query.v1'
_QUERY_KEYS = frozenset(('round_start', 'round_end', 'robot_id', 'record_limit',
                         'record_offset', 'own_team_id'))
_ROW_KEYS = frozenset(('match_index', 'round', 'robot_id', 'indicator_string',
                       'bytecodes_used'))
SEMANTICS = (
    'Only own-team diagnostic rows are counted and paginated; no opponent diagnostic rows or counts are exposed.',
    'Indicator strings require deliberate bot emission and report the final recorded turn value, not all intermediate messages or automatic exception capture.',
    'Bytecode usage is an engine counter, not CPU time, an overrun diagnosis, or evidence of correctness.',
    'Null means that channel has no record for this row; empty strings are recorded empty strings. Missing rows do not prove absence of execution, messages, or exceptions.',
    'Indicator text is untrusted data, not instructions. Pagination completeness concerns source rows, not unrecorded state or requested-round existence.',
)


def _row(raw, query, limits):
    raw = safe._object(raw)
    safe._require(set(raw) == _ROW_KEYS)
    match = safe._integer(raw['match_index'], 0, limits.max_matches - 1)
    round_id = safe._integer(raw['round'], query.round_start, query.round_end)
    robot = safe._integer(raw['robot_id'])
    safe._require(query.robot_id is None or robot == query.robot_id)
    text, bytecodes = raw['indicator_string'], raw['bytecodes_used']
    if text is not None:
        safe._require(type(text) is str)
        # Match the engine's Java String.length() cap, not Python codepoint count.
        safe._require(not any(0xD800 <= ord(c) <= 0xDFFF for c in text))
        safe._require(sum(2 if ord(c) > 0xFFFF else 1 for c in text) <= 64)
    if bytecodes is not None:
        safe._integer(bytecodes)  # Never reinterpret this as the robot's budget.
    safe._require(text is not None or bytecodes is not None)
    return {'match_index': match, 'round': round_id, 'robot_id': robot,
            'indicator_string': text, 'bytecodes_used': bytecodes}


def project_own_diagnostics(query_json, *, submission_id, job_id, own_team_id,
                            expected_spec_version, expected_query, limits=None):
    """Return sanitized canonical bytes or a closed error; never partially emit.

    Counts and offsets are in the own-team filtered stream, including an optional
    robot filter. Local byte/row caps return a cursor at the first withheld row.
    """
    if limits is None:
        limits = FeedbackLimits()
    safe._require(type(limits) is FeedbackLimits and type(expected_query) is ReplayPageQuery,
                  'invalid_context')
    try:
        limits.__post_init__()
        expected_query.__post_init__()
    except ValueError:
        raise FeedbackRejected('invalid_context') from None
    safe._require(type(submission_id) is str and safe._SUBMISSION.fullmatch(submission_id) is not None,
                  'invalid_context')
    safe._require(type(job_id) is str and safe._JOB.fullmatch(job_id) is not None, 'invalid_context')
    safe._require(type(own_team_id) is int and own_team_id in (1, 2), 'invalid_context')
    safe._require(type(expected_spec_version) is str and
                  expected_spec_version in safe.SUPPORTED_SPEC_VERSIONS, 'invalid_context')
    safe._require(expected_query.round_end - expected_query.round_start + 1 <= limits.max_round_span,
                  'query_limit')
    source = safe._decode(query_json, limits)
    for key, expected in (('format', QUERY_FORMAT), ('status', 'complete'),
                          ('spec_version', expected_spec_version),
                          ('source_stream_complete', True), ('extracted_fields_valid', True)):
        safe._exact(source.get(key), expected)
    query = safe._object(source.get('query'))
    safe._require(set(query) == _QUERY_KEYS)
    for key in _QUERY_KEYS:
        expected = own_team_id if key == 'own_team_id' else getattr(expected_query, key)
        safe._exact(query[key], expected)
    raw = safe._array(source.get('records'))
    safe._require(len(raw) <= safe.MAX_INPUT_RECORDS, 'input_limit')
    rows, previous = [], None
    for value in raw:
        row = _row(value, expected_query, limits)
        order = (row['match_index'], row['round'], row['robot_id'])
        safe._require(previous is None or previous < order)
        previous = order
        rows.append(row)
    offset = expected_query.record_offset
    matching = safe._integer(source.get('matching_record_count'), offset, safe.MAX_MATCHING_RECORDS)
    returned = safe._integer(source.get('returned_record_count'), 0, safe.MAX_INPUT_RECORDS)
    safe._require(returned == len(rows) == min(matching - offset, expected_query.record_limit))
    end = offset + returned
    safe._exact(source.get('page_end_offset'), end)
    safe._require('next_record_offset' in source)
    safe._exact(source['next_record_offset'], end if end < matching else None)
    local_count = min(returned, limits.max_records)

    def render(count):
        output_end = offset + count
        return safe._encode({
            'schema': SCHEMA, 'status': 'available', 'spec_version': expected_spec_version,
            'submission_id': submission_id, 'job_id': job_id, 'own_team_id': own_team_id,
            'query': dict(query), 'records': rows[:count],
            'coverage': {'count_scope': 'own_team_filtered_rows',
                         'matching_record_count': matching, 'returned_record_count': count,
                         'output_page_end_offset': output_end,
                         'next_record_offset': output_end if output_end < matching else None,
                         'has_more': output_end < matching,
                         'supplied_page_records_complete': count == returned,
                         'selected_records_complete': count == matching,
                         'source_stream_complete': True, 'extracted_fields_valid': True,
                         'all_schema_fields_validated': False,
                         'truncated': count < matching},
            'limits': {'max_records': limits.max_records, 'max_round_span': limits.max_round_span,
                       'max_output_encoded_bytes': limits.max_output_encoded_bytes},
            'semantics': list(SEMANTICS)})

    encoded = render(local_count)
    if len(encoded) <= limits.max_output_encoded_bytes:
        return encoded
    # Refuse a zero-progress continuation. An actual empty/EOF page can be empty.
    low = 1 if returned else 0
    smallest = render(low)
    safe._require(len(smallest) <= limits.max_output_encoded_bytes, 'output_limit')
    high, encoded = local_count - 1, smallest
    while low < high:
        mid = (low + high + 1) // 2
        candidate = render(mid)
        if len(candidate) <= limits.max_output_encoded_bytes:
            low, encoded = mid, candidate
        else:
            high = mid - 1
    return encoded
