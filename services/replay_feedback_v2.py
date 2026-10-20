"""Trusted-side BC23 page projection; no IO, authorization or source authenticity.

V1 remains unchanged. A host must obtain each actual slice from the same complete
immutable source, bind it to its owner/run/view, and enforce aggregate budgets.
"""
from collections import Counter
from dataclasses import dataclass

from services import replay_feedback as v1
from services.replay_feedback import FeedbackLimits, FeedbackRejected


SCHEMA = 'bcenv.bc2023.replay-feedback.v2'
QUERY_FORMAT = 'bcenv.bc2023.replay-query.v2'


@dataclass(frozen=True)
class ReplayPageQuery:
    """Exact host-authorized selection, including offset after window/filter."""
    round_start: int
    round_end: int
    robot_id: int | None
    record_offset: int
    record_limit: int

    def __post_init__(self):
        good = (type(self.round_start) is int and type(self.round_end) is int and
                0 <= self.round_start <= self.round_end <= v1.INT_MAX and
                self.round_end - self.round_start + 1 <= v1.MAX_ROUND_SPAN and
                (self.robot_id is None or (type(self.robot_id) is int and
                 0 <= self.robot_id <= v1.INT_MAX)) and
                type(self.record_offset) is int and
                0 <= self.record_offset <= v1.MAX_MATCHING_RECORDS and
                type(self.record_limit) is int and 1 <= self.record_limit <= v1.MAX_INPUT_RECORDS)
        if not good:
            raise ValueError('invalid_replay_page_query')


def _records(source, matches, query, own_team_id):
    source = v1._array(source)
    v1._require(len(source) <= v1.MAX_INPUT_RECORDS, 'input_limit')
    records, previous, event_context, round_events = [], None, {}, {}
    resource_teams = set()
    for raw in source:
        row = v1._record(raw, matches, query, own_team_id)
        order = (row['match_index'], row['event_index'], v1._GROUP[row['kind']], row['row_index'])
        v1._require(previous is None or previous < order)
        if records:
            prior = records[-1]
            v1._require(row['event_index'] >= prior['event_index'])
            if row['match_index'] == prior['match_index']:
                v1._require(row['round'] >= prior['round'])
        previous = order
        context = (row['match_index'], row['round'])
        v1._require(event_context.setdefault(row['event_index'], context) == context)
        v1._require(round_events.setdefault(context, row['event_index']) == row['event_index'])
        if row['kind'] == 'team_resource_delta':
            identity = (row['event_index'], row['team_id'])
            v1._require(identity not in resource_teams)
            if row['row_index'] == 1:
                # Only the first returned row can have its row0 before this page.
                v1._require((not records and query['record_offset'] > 0) or
                            any((row['event_index'], team) in resource_teams for team in (1, 2)))
            resource_teams.add(identity)
        records.append(row)
    return records


def _record_coverage(records, matches, offset, end, matching):
    """Check wholly visible intervals; do not assume missing boundary rows absent."""
    observed = [set() for _ in matches]
    resource_counts, resource_teams = Counter(), {}
    for row in records:
        context = (row['match_index'], row['round'])
        if row['round']:
            observed[row['match_index']].add(row['round'])
        if row['kind'] == 'team_resource_delta':
            resource_counts[row['match_index']] += 1
            resource_teams.setdefault(context, set()).add(row['team_id'])
    first, last = (records[0], records[-1]) if records else (None, None)
    first_context = (first['match_index'], first['round']) if first else None
    last_context = (last['match_index'], last['round']) if last else None
    resource_group = v1._GROUP['team_resource_delta']
    left_resources_complete = offset == 0 or (first is not None and (
        v1._GROUP[first['kind']] < resource_group or
        (first['kind'] == 'team_resource_delta' and first['row_index'] == 0)))
    right_resources_complete = end == matching or (last is not None and (
        v1._GROUP[last['kind']] > resource_group or
        (last['kind'] == 'team_resource_delta' and last['row_index'] == 1)))
    for match in matches:
        index = match['match_index']
        v1._require(len(observed[index]) <= match['selected_round_events'])
        if not match['source_resource_records_cover_observed_rounds']:
            continue
        left_match_complete = offset == 0 or (first is not None and index > first['match_index'])
        right_match_complete = end == matching or (last is not None and index < last['match_index'])
        if left_match_complete and right_match_complete:
            v1._require(resource_counts[index] == 2 * match['selected_round_events'])
        for round_id in observed[index]:
            context = (index, round_id)
            if ((context != first_context or left_resources_complete) and
                    (context != last_context or right_resources_complete)):
                v1._require(resource_teams.get(context, set()) == {1, 2})
        # In a known contiguous interval, complete omitted rounds are detectable,
        # including those with no returned row. At most MAX_ROUND_SPAN iterations.
        start, stop = match['first_selected_round'], match['last_selected_round']
        if start is None or stop - start + 1 != match['selected_round_events']:
            continue
        if not left_match_complete:
            if first is None or index != first['match_index']:
                continue
            start = max(start, first['round'] + int(not left_resources_complete))
        if not right_match_complete:
            if last is None or index != last['match_index']:
                continue
            stop = min(stop, last['round'] - int(not right_resources_complete))
        for round_id in range(start, stop + 1):
            v1._require(resource_teams.get((index, round_id), set()) == {1, 2})


def project_replay_feedback_page(query_json, *, submission_id, job_id, own_team_id,
                                 expected_spec_version, expected_query, limits=None):
    """Return canonical sanitized v2 bytes with exact progress, or a closed error.

    The expected query is trusted host context, not inferred from source metadata.
    Continuation uses coverage.next_record_offset, never source_next_record_offset.
    All supplied rows are validated even when local limits will withhold them.
    """
    if limits is None:
        limits = FeedbackLimits()
    v1._require(type(limits) is FeedbackLimits and type(expected_query) is ReplayPageQuery,
                'invalid_context')
    try:
        limits.__post_init__()
        expected_query.__post_init__()
    except ValueError:
        raise FeedbackRejected('invalid_context') from None
    v1._require(type(submission_id) is str and v1._SUBMISSION.fullmatch(submission_id) is not None,
                'invalid_context')
    v1._require(type(job_id) is str and v1._JOB.fullmatch(job_id) is not None, 'invalid_context')
    v1._require(type(own_team_id) is int and own_team_id in (1, 2), 'invalid_context')
    v1._require(type(expected_spec_version) is str and
                expected_spec_version in v1.SUPPORTED_SPEC_VERSIONS, 'invalid_context')
    source = v1._decode(query_json, limits)
    v1._exact(source.get('format'), QUERY_FORMAT)
    v1._exact(source.get('spec_version'), expected_spec_version)
    v1._exact(source.get('status'), 'complete')
    v1._exact(source.get('source_stream_complete'), True)
    v1._exact(source.get('extracted_fields_valid'), True)
    v1._exact(source.get('all_schema_fields_validated'), False)
    query = v1._query(source.get('query'), limits)
    offset = v1._integer(source['query'].get('record_offset'), 0, v1.MAX_MATCHING_RECORDS)
    query['record_offset'] = offset
    for key in ('round_start', 'round_end', 'robot_id', 'record_offset'):
        v1._exact(query[key], getattr(expected_query, key))
    v1._exact(query['source_record_limit'], expected_query.record_limit)
    raw_matches = v1._array(source.get('matches'))
    v1._require(1 <= len(raw_matches) <= limits.max_matches, 'query_limit')
    matches = [v1._match(value, index, query) for index, value in enumerate(raw_matches)]
    records = _records(source.get('records'), matches, query, own_team_id)
    matching = v1._integer(source.get('matching_record_count'), 0, v1.MAX_MATCHING_RECORDS)
    v1._require(offset <= matching)
    returned = v1._integer(source.get('returned_record_count'), 0, v1.MAX_INPUT_RECORDS)
    v1._require(returned == len(records) == min(matching - offset, query['source_record_limit']))
    end = offset + returned
    v1._exact(source.get('page_records_complete'), True)
    v1._exact(source.get('page_end_offset'), end)
    v1._require('next_record_offset' in source)
    source_next = end if end < matching else None
    v1._exact(source['next_record_offset'], source_next)
    v1._exact(source.get('truncated'), returned < matching)
    v1._exact(source.get('selected_records_complete'), returned == matching)
    counts_source = v1._object(source.get('matching_counts_by_kind'))
    counts = {kind: v1._integer(counts_source[kind], 0, v1.MAX_MATCHING_RECORDS)
              for kind in v1.KINDS if kind in counts_source}
    v1._require(sum(counts.values()) == matching)
    actual = Counter(row['kind'] for row in records)
    v1._require(all(actual[kind] <= counts.get(kind, 0) for kind in v1.KINDS))
    resource_count = counts.get('team_resource_delta', 0)
    v1._require(resource_count <= 2 * sum(match['selected_round_events'] for match in matches))
    v1._require(resource_count >= 2 * sum(match['selected_round_events'] for match in matches
                                         if match['source_resource_records_cover_observed_rounds']))
    if returned == matching:
        v1._require(all(actual[kind] == counts.get(kind, 0) for kind in v1.KINDS))
    _record_coverage(records, matches, offset, end, matching)
    local_count = min(returned, limits.max_records)

    def render(count):
        output_end = offset + count
        has_more = output_end < matching
        reasons = []
        if offset:
            reasons.append('source_record_offset')
        if source_next is not None:
            reasons.append('source_record_limit')
        if returned > local_count:
            reasons.append('projection_record_limit')
        if count < local_count:
            reasons.append('projection_output_limit')
        return v1._encode({
            'schema': SCHEMA, 'status': 'available', 'spec_version': expected_spec_version,
            'submission_id': submission_id, 'job_id': job_id, 'own_team_id': own_team_id,
            'query': query, 'matches': matches, 'records': records[:count],
            'coverage': {
                'source_stream_complete': True, 'extracted_fields_valid': True,
                'all_schema_fields_validated': False,
                'matching_record_count': matching, 'matching_counts_by_kind': counts,
                'source_returned_record_count': returned, 'returned_record_count': count,
                'source_page_records_complete': True, 'source_page_end_offset': end,
                'source_next_record_offset': source_next, 'output_page_end_offset': output_end,
                'next_record_offset': output_end if has_more else None, 'has_more': has_more,
                'supplied_page_records_complete': count == returned,
                'selected_records_complete': count == matching,
                'truncated': count < matching, 'truncation_reasons': reasons},
            'limits': {'max_records': limits.max_records, 'max_round_span': limits.max_round_span,
                       'max_output_encoded_bytes': limits.max_output_encoded_bytes}})

    encoded = render(local_count)
    if len(encoded) <= limits.max_output_encoded_bytes:
        return encoded
    # Never issue a continuation without progress. EOF/empty may return zero rows.
    low = 1 if returned else 0
    smallest = render(low)
    v1._require(len(smallest) <= limits.max_output_encoded_bytes, 'output_limit')
    high, encoded = local_count - 1, smallest
    while low < high:
        mid = (low + high + 1) // 2
        candidate = render(mid)
        if len(candidate) <= limits.max_output_encoded_bytes:
            low, encoded = mid, candidate
        else:
            high = mid - 1
    return encoded
