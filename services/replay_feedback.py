"""Bounded BC23 raw-event projection for a future trusted host (stdlib only).

Not wired to the scrimmage facade. This accepts the JSON output of ReplayQuery,
not a replay, summary, URL or path. The caller must separately establish parser
success, immutable evidence, authorization, ownership and the own-team mapping.
Shape validation here cannot establish authenticity or enforce an isolation
boundary. Never package services/ into the competitor runtime.
"""
from collections import Counter
from dataclasses import dataclass
import json
import re


SCHEMA = 'bcenv.bc2023.replay-feedback.v1'
QUERY_FORMAT = 'bcenv.bc2023.replay-query.v1'
SUPPORTED_SPEC_VERSIONS = frozenset(('2.0.3', '3.0.13'))
INT_MAX = 2_147_483_647
INT_MIN = -2_147_483_648
MAX_INPUT_ENCODED_BYTES = 4_194_304
MAX_INPUT_RECORDS = 10_000
MAX_RECORDS = 1_024
MAX_ROUND_SPAN = 256
MAX_OUTPUT_ENCODED_BYTES = 262_144
MAX_MATCHES = 8
MAX_JSON_DEPTH = 32
MAX_MATCHING_RECORDS = 5_000_000  # ReplaySummary's core-array budget.
MAX_EVENTS = 100_000

# Pinned schema/battlecode.fbs at 5d142a87579af22c2c04ce046a3cbd448dc1869e.
# These are the schema enum orders, not RobotType or Java enum ordinals.
# Released 2.0.3 and 3.0.13 schema classes are byte-identical; host policy still
# selects one expected version per call, with no automatic version inference.
BODY_TYPES = ('HEADQUARTERS', 'CARRIER', 'LAUNCHER', 'AMPLIFIER',
              'DESTABILIZER', 'BOOSTER')
ACTIONS = ('LAUNCH_ATTACK', 'THROW_ATTACK', 'SPAWN_UNIT', 'PICK_UP_RESOURCE',
           'PLACE_RESOURCE', 'DESTABILIZE', 'DESTABILIZE_DAMAGE', 'BOOST',
           'BUILD_ANCHOR', 'PICK_UP_ANCHOR', 'PLACE_ANCHOR', 'CHANGE_HEALTH',
           'CHANGE_ADAMANTIUM', 'CHANGE_MANA', 'CHANGE_ELIXIR', 'DIE_EXCEPTION')
KINDS = ('initial_body', 'spawn', 'team_resource_delta', 'movement', 'action', 'death')
_GROUP = {kind: index for index, kind in enumerate(KINDS)}
_SUBMISSION = re.compile(r's_[0-9a-f]{32}\Z')
_JOB = re.compile(r'j_[0-9a-f]{32}\Z')
_REJECTIONS = frozenset(('invalid_evidence', 'invalid_context', 'input_limit',
                         'query_limit', 'output_limit'))


class FeedbackRejected(ValueError):
    """Closed reason code only; never includes parser errors or source values."""

    def __init__(self, code='invalid_evidence'):
        self.code = code if code in _REJECTIONS else 'invalid_evidence'
        super().__init__(self.code)


@dataclass(frozen=True)
class FeedbackLimits:
    """Trusted caller may tighten limits, never enlarge the hard ceilings."""
    max_input_encoded_bytes: int = MAX_INPUT_ENCODED_BYTES
    max_records: int = MAX_RECORDS
    max_round_span: int = MAX_ROUND_SPAN
    max_output_encoded_bytes: int = MAX_OUTPUT_ENCODED_BYTES
    max_matches: int = MAX_MATCHES

    def __post_init__(self):
        ceilings = (MAX_INPUT_ENCODED_BYTES, MAX_RECORDS, MAX_ROUND_SPAN,
                    MAX_OUTPUT_ENCODED_BYTES, MAX_MATCHES)
        values = (self.max_input_encoded_bytes, self.max_records,
                  self.max_round_span, self.max_output_encoded_bytes, self.max_matches)
        if any(type(value) is not int or not 1 <= value <= ceiling
               for value, ceiling in zip(values, ceilings)):
            raise ValueError('invalid_feedback_limits')


def _require(condition, code='invalid_evidence'):
    if not condition:
        raise FeedbackRejected(code)


def _integer(value, low=0, high=INT_MAX):
    _require(type(value) is int and low <= value <= high)
    return value


def _boolean(value):
    _require(type(value) is bool)
    return value


def _object(value):
    _require(type(value) is dict)
    return value


def _array(value):
    _require(type(value) is list)
    return value


def _exact(value, expected):
    _require(type(value) is type(expected) and value == expected)
    return expected


def _json_integer(token):
    # Bound conversion before int(), including integers in discarded metadata.
    _require(len(token) <= 11)
    return _integer(int(token), INT_MIN, INT_MAX)


def _no_float(_token):
    raise FeedbackRejected()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _decode(data, limits):
    _require(type(data) is bytes)
    _require(len(data) <= limits.max_input_encoded_bytes, 'input_limit')
    try:
        text = data.decode('utf-8')
        # Refuse excessive nesting before the JSON decoder allocates containers.
        # This is only a depth scan; json.loads still validates the full syntax.
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
                _require(depth <= MAX_JSON_DEPTH, 'input_limit')
            elif char in ']}':
                depth -= 1
        return _object(json.loads(text, object_pairs_hook=_unique_object,
                                  parse_int=_json_integer, parse_float=_no_float,
                                  parse_constant=_no_float))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise FeedbackRejected() from None


def _query(source, limits):
    source = _object(source)
    start = _integer(source.get('round_start'))
    end = _integer(source.get('round_end'), start)
    _require(end - start + 1 <= limits.max_round_span, 'query_limit')
    robot = source.get('robot_id')
    _require('robot_id' in source)
    if robot is not None:
        _integer(robot)
    limit = _integer(source.get('record_limit'), 1, MAX_INPUT_RECORDS)
    _exact(source.get('round_bounds_inclusive'), True)
    _exact(source.get('team_resource_rows_included_with_robot_filter'), True)
    _exact(source.get('match_scope'), 'all matches, separately indexed')
    return {'round_start': start, 'round_end': end, 'robot_id': robot,
            'round_bounds_inclusive': True, 'source_record_limit': limit,
            'team_resource_rows_included_with_robot_filter': True,
            'match_scope': 'all_matches'}


def _match(source, index, query):
    source = _object(source)
    _exact(source.get('match_index'), index)
    count = _integer(source.get('observed_round_events'), 0, MAX_EVENTS)
    last = _integer(source.get('last_observed_round'))
    total = _integer(source.get('reported_total_rounds'), last)
    footer = _exact(source.get('match_footer_present'), True)
    contiguous = _boolean(source.get('source_round_ids_contiguous'))
    covered = _boolean(source.get('source_round_records_cover_reported_rounds'))
    resources = _boolean(source.get('source_resource_records_cover_observed_rounds'))
    _require(count <= last and ((count == 0) == (last == 0)))
    _require(contiguous == (count == last))
    _require(covered == (contiguous and last == total))

    start, end = query['round_start'], query['round_end']
    positive_start = max(1, start)
    requested = max(0, end - positive_start + 1)
    selected = _integer(source.get('selected_round_events'), 0, min(requested, count))
    _require('first_selected_round' in source and 'last_selected_round' in source)
    first, final = source['first_selected_round'], source['last_selected_round']
    if selected:
        _integer(first, positive_start, min(end, last))
        _integer(final, first, min(end, last))
        _require(selected <= final - first + 1)
        _require((selected == 1) == (first == final))
    else:
        _require(first is None and final is None)
    if contiguous:
        expected = max(0, min(end, last) - positive_start + 1)
        _require(selected == expected)
        if selected:
            _require(first == positive_start and final == min(end, last))
    present = _boolean(source.get('all_requested_positive_rounds_present'))
    _require(present == (selected == requested))
    return {'match_index': index, 'observed_round_events': count,
            'last_observed_round': last, 'reported_total_rounds': total,
            'match_footer_present': footer, 'source_round_ids_contiguous': contiguous,
            'source_round_records_cover_reported_rounds': covered,
            'source_resource_records_cover_observed_rounds': resources,
            'selected_round_events': selected, 'first_selected_round': first,
            'last_selected_round': final, 'all_requested_positive_rounds_present': present}


def _record(source, matches, query, own_team_id):
    source = _object(source)
    kind = source.get('kind')
    _require(type(kind) is str and kind in KINDS)
    match = _integer(source.get('match_index'), 0, len(matches) - 1)
    event = _integer(source.get('event_index'), 1, MAX_EVENTS - 1)
    round_id = _integer(source.get('round'), query['round_start'], query['round_end'])
    row_index = _integer(source.get('row_index'), 0, MAX_MATCHING_RECORDS - 1)
    _require((kind == 'initial_body') == (round_id == 0))
    coverage = matches[match]
    if round_id:
        _require(coverage['selected_round_events'] > 0)
        _require(coverage['first_selected_round'] <= round_id <= coverage['last_selected_round'])
    result = {'kind': kind, 'match_index': match, 'event_index': event,
              'round': round_id, 'row_index': row_index}

    if kind == 'team_resource_delta':
        team = _integer(source.get('team_id'), 1, 2)
        _require(row_index <= 1)  # ReplaySummary permits at most one row per team.
        for field in ('adamantium_delta', 'mana_delta', 'elixir_delta'):
            result[field] = _integer(source.get(field), INT_MIN, INT_MAX)
    else:
        robot = _integer(source.get('robot_id'))
        _require(query['robot_id'] is None or robot == query['robot_id'])
        identity = ('team_id', 'body_type_code', 'body_type', 'identity_observed_round')
        _require(all(field in source for field in identity))
        team = source['team_id']
        if team is None:
            _require(kind not in ('initial_body', 'spawn'))
            _require(all(source[field] is None for field in identity))
            body_code = body_type = observed = None
        else:
            _integer(team, 1, 2)
            body_code = _integer(source['body_type_code'], 0, len(BODY_TYPES) - 1)
            body_type = _exact(source['body_type'], BODY_TYPES[body_code])
            observed = _integer(source['identity_observed_round'], 0, round_id)
            if kind in ('initial_body', 'spawn'):
                _require(observed == round_id)
        result.update(robot_id=robot, body_type_code=body_code, body_type=body_type,
                      identity_observed_round=observed)
    result.update(team_id=team, team_slot=None if team is None else
                  'own' if team == own_team_id else 'opponent')

    if kind in ('initial_body', 'spawn', 'movement'):
        result.update(x=_integer(source.get('x'), INT_MIN, INT_MAX),
                      y=_integer(source.get('y'), INT_MIN, INT_MAX))
    elif kind == 'action':
        code = _integer(source.get('action_code'), 0, len(ACTIONS) - 1)
        result.update(action_code=code, action=_exact(source.get('action'), ACTIONS[code]),
                      raw_target=_integer(source.get('raw_target'), INT_MIN, INT_MAX))
    return result


def _records(source, matches, query, own_team_id):
    source = _array(source)
    _require(len(source) <= MAX_INPUT_RECORDS, 'input_limit')
    records, previous, event_context, round_events = [], None, {}, {}
    resource_teams = set()
    for raw in source:
        row = _record(raw, matches, query, own_team_id)
        order = (row['match_index'], row['event_index'], _GROUP[row['kind']], row['row_index'])
        _require(previous is None or previous < order)
        if records:
            prior = records[-1]
            _require(row['event_index'] >= prior['event_index'])
            if row['match_index'] == prior['match_index']:
                _require(row['round'] >= prior['round'])
        previous = order
        context = (row['match_index'], row['round'])
        _require(event_context.setdefault(row['event_index'], context) == context)
        _require(round_events.setdefault(context, row['event_index']) == row['event_index'])
        if row['kind'] == 'team_resource_delta':
            identity = (row['event_index'], row['team_id'])
            _require(identity not in resource_teams)
            if row['row_index'] == 1:
                _require(any((row['event_index'], team) in resource_teams for team in (1, 2)))
            resource_teams.add(identity)
        records.append(row)
    return records


def _record_coverage(records, matches, source_truncated):
    """Reject contradictions visible in the supplied prefix, not infer gaps."""
    observed = [set() for _ in matches]
    resource_counts, resource_teams = Counter(), {}
    for row in records:
        context = (row['match_index'], row['round'])
        if row['round']:
            observed[row['match_index']].add(row['round'])
        if row['kind'] == 'team_resource_delta':
            resource_counts[row['match_index']] += 1
            resource_teams.setdefault(context, set()).add(row['team_id'])
    last = records[-1] if records else None
    last_context = (last['match_index'], last['round']) if last else None
    last_resource_group_complete = last is not None and (
        _GROUP[last['kind']] > _GROUP['team_resource_delta'] or
        (last['kind'] == 'team_resource_delta' and last['row_index'] == 1))
    for match in matches:
        index = match['match_index']
        _require(len(observed[index]) <= match['selected_round_events'])
        if not match['source_resource_records_cover_observed_rounds']:
            continue
        complete_match = not source_truncated or (last is not None and index < last['match_index'])
        if complete_match:
            _require(resource_counts[index] == 2 * match['selected_round_events'])
        for round_id in observed[index]:
            context = (index, round_id)
            if not source_truncated or context != last_context or last_resource_group_complete:
                _require(resource_teams.get(context, set()) == {1, 2})
        # When coverage establishes a contiguous selected interval, even an
        # otherwise empty skipped round before the prefix boundary is detectable.
        first, final = match['first_selected_round'], match['last_selected_round']
        if (last is not None and index == last['match_index'] and first is not None and
                final - first + 1 == match['selected_round_events']):
            end = min(final, last['round'] - int(source_truncated and not last_resource_group_complete))
            for round_id in range(first, end + 1):
                _require(resource_teams.get((index, round_id), set()) == {1, 2})


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


def project_replay_feedback(query_json, *, submission_id, job_id, own_team_id,
                            expected_spec_version, limits=None):
    """Return bounded canonical JSON bytes, or raise FeedbackRejected with no rows.

    Handles and slot mapping are trusted-host arguments, never read from source
    metadata. The trusted host must explicitly select the supported replay spec
    version; source evidence cannot choose it or silently pool versions.
    Unknown keys are discarded at every output level; unknown record kinds and
    invalid retained fields reject the whole projection, including beyond caps.
    Source and projection caps retain an unfiltered presentation-order prefix.
    """
    if limits is None:
        limits = FeedbackLimits()
    _require(type(limits) is FeedbackLimits, 'invalid_context')
    # Recheck even frozen dataclasses: no configuration object can raise ceilings.
    try:
        limits.__post_init__()
    except ValueError:
        raise FeedbackRejected('invalid_context') from None
    _require(type(submission_id) is str and _SUBMISSION.fullmatch(submission_id) is not None,
             'invalid_context')
    _require(type(job_id) is str and _JOB.fullmatch(job_id) is not None, 'invalid_context')
    _require(type(own_team_id) is int and own_team_id in (1, 2), 'invalid_context')
    _require(type(expected_spec_version) is str and expected_spec_version in SUPPORTED_SPEC_VERSIONS,
             'invalid_context')
    source = _decode(query_json, limits)
    _exact(source.get('format'), QUERY_FORMAT)
    _exact(source.get('spec_version'), expected_spec_version)
    _exact(source.get('status'), 'complete')
    _exact(source.get('source_stream_complete'), True)
    _exact(source.get('extracted_fields_valid'), True)
    _exact(source.get('all_schema_fields_validated'), False)
    query = _query(source.get('query'), limits)
    raw_matches = _array(source.get('matches'))
    _require(1 <= len(raw_matches) <= limits.max_matches, 'query_limit')
    matches = [_match(value, index, query) for index, value in enumerate(raw_matches)]
    # Validate every input row before applying any output truncation.
    records = _records(source.get('records'), matches, query, own_team_id)
    matching = _integer(source.get('matching_record_count'), 0, MAX_MATCHING_RECORDS)
    returned = _integer(source.get('returned_record_count'), 0, MAX_INPUT_RECORDS)
    _require(returned == len(records) == min(matching, query['source_record_limit']))
    counts_source = _object(source.get('matching_counts_by_kind'))
    counts = {kind: _integer(counts_source[kind], 0, MAX_MATCHING_RECORDS)
              for kind in KINDS if kind in counts_source}
    _require(sum(counts.values()) == matching)
    actual = Counter(row['kind'] for row in records)
    _require(all(actual[kind] <= counts.get(kind, 0) for kind in KINDS))
    resource_count = counts.get('team_resource_delta', 0)
    _require(resource_count <= 2 * sum(match['selected_round_events'] for match in matches))
    _require(resource_count >= 2 * sum(match['selected_round_events'] for match in matches
                                      if match['source_resource_records_cover_observed_rounds']))
    if all(match['source_resource_records_cover_observed_rounds'] for match in matches):
        _require(resource_count == 2 * sum(match['selected_round_events'] for match in matches))
    source_truncated = _boolean(source.get('truncated'))
    _require(source_truncated == (matching > returned))
    _exact(source.get('selected_records_complete'), not source_truncated)
    _record_coverage(records, matches, source_truncated)
    if not source_truncated:
        _require(all(actual[kind] == counts.get(kind, 0) for kind in KINDS))

    local_count = min(returned, limits.max_records)

    def render(count):
        reasons = []
        if source_truncated:
            reasons.append('source_record_limit')
        if returned > local_count:
            reasons.append('projection_record_limit')
        if count < local_count:
            reasons.append('projection_output_limit')
        result = {'schema': SCHEMA, 'status': 'available', 'spec_version': expected_spec_version,
                  'submission_id': submission_id, 'job_id': job_id, 'own_team_id': own_team_id,
                  'query': query, 'matches': matches, 'records': records[:count],
                  'coverage': {'source_stream_complete': True, 'extracted_fields_valid': True,
                               'all_schema_fields_validated': False,
                               'matching_record_count': matching,
                               'matching_counts_by_kind': counts,
                               'source_returned_record_count': returned,
                               'returned_record_count': count,
                               'source_truncated': source_truncated,
                               'projection_truncated': count < returned,
                               'truncated': count < matching,
                               'selected_records_complete': count == matching,
                               'truncation_reasons': reasons},
                  'limits': {'max_records': limits.max_records,
                             'max_round_span': limits.max_round_span,
                             'max_output_encoded_bytes': limits.max_output_encoded_bytes}}
        return _encode(result)

    encoded = render(local_count)
    if len(encoded) <= limits.max_output_encoded_bytes:
        return encoded
    # Byte truncation is whole-row prefix truncation. Coverage is never elided.
    empty = render(0)
    _require(len(empty) <= limits.max_output_encoded_bytes, 'output_limit')
    low, high, encoded = 0, local_count - 1, empty
    while low < high:
        mid = (low + high + 1) // 2
        candidate = render(mid)
        if len(candidate) <= limits.max_output_encoded_bytes:
            low, encoded = mid, candidate
        else:
            high = mid - 1
    return encoded
