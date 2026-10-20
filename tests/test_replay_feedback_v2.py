"""Synthetic page fixtures derived from the official-schema Java fixture.

No engine game, provider, heldout or listener is used. Java independently tests
FlatBuffers pagination; these fixtures test the trusted-side JSON projection.
"""
from collections import Counter
from copy import deepcopy
import json
import unittest

from services import replay_feedback as v1
from services import replay_feedback_v2 as v2
from test_replay_feedback import fixture, encode, SUBMISSION, JOB, SECRET


def page(full=None, offset=0, limit=100, **kwargs):
    if full is None:
        full = fixture(limit=10000, **kwargs)
    value = deepcopy({k: v for k, v in full.items() if k != 'records'})
    count = full['matching_record_count']
    rows = deepcopy(full['records'][offset:offset + limit])
    value.update(format=v2.QUERY_FORMAT, records=rows, returned_record_count=len(rows),
                 truncated=len(rows) < count, selected_records_complete=len(rows) == count,
                 page_records_complete=True, page_end_offset=offset + len(rows),
                 next_record_offset=offset + len(rows) if offset + len(rows) < count else None)
    value['query'].update(record_offset=offset, record_limit=limit)
    return value


def query(value):
    q = value['query']
    return v2.ReplayPageQuery(q['round_start'], q['round_end'], q['robot_id'],
                             q['record_offset'], q['record_limit'])


def project(value, *, expected_spec_version='2.0.3', expected_query=None, **kwargs):
    return v2.project_replay_feedback_page(encode(value), submission_id=SUBMISSION, job_id=JOB,
        own_team_id=1, expected_spec_version=expected_spec_version,
        expected_query=expected_query if expected_query is not None else query(value), **kwargs)


def long_fixture():
    full = fixture(start=1, end=1, robot=42, limit=10000)
    template = full['records'][-1]
    rows = full['records'][:2]
    for i in range(12050):
        row = dict(template, row_index=i, action_code=11, action='CHANGE_HEALTH', raw_target=-1)
        if i == 12049:
            row.update(action_code=15, action='DIE_EXCEPTION', raw_target=0)
        rows.append(row)
    rows.append({k: v for k, v in dict(template, kind='death', row_index=0).items()
                 if k not in ('action', 'action_code', 'raw_target')})
    full.update(records=rows, matching_record_count=len(rows),
                matching_counts_by_kind=dict(Counter(row['kind'] for row in rows)))
    return full


class PageFeedbackTests(unittest.TestCase):
    def reject(self, value, code='invalid_evidence', **kwargs):
        with self.assertRaises(v2.FeedbackRejected) as caught:
            project(value, **kwargs)
        self.assertEqual(str(caught.exception), code)

    def test_all_offsets_and_boundaries_match_exact_source_rows(self):
        for options in ({}, {'matches': 2}, {'start': 0, 'end': 2, 'robot': None},
                        {'start': 33, 'end': 41, 'robot': None, 'gap': True}):
            full = fixture(limit=10000, **options)
            for offset in range(len(full['records']) + 1):
                for limit in (1, 2, 5, 100):
                    with self.subTest(options=options, offset=offset, limit=limit):
                        value = page(full, offset, limit)
                        output = json.loads(project(value))
                        expected = deepcopy(value['records'])
                        for row in expected:
                            team = row['team_id']
                            row['team_slot'] = None if team is None else 'own' if team == 1 else 'opponent'
                        self.assertEqual(output['records'], expected)
                        cov = output['coverage']
                        self.assertEqual(cov['next_record_offset'], value['next_record_offset'])
                        self.assertEqual(cov['matching_counts_by_kind'], full['matching_counts_by_kind'])
                        self.assertEqual(cov['selected_records_complete'], len(expected) == len(full['records']))

    def test_record_and_byte_caps_concatenate_without_skips(self):
        full = fixture(robot=None, matches=2, limit=10000)
        for limits in (v2.FeedbackLimits(max_records=3),
                       v2.FeedbackLimits(max_records=20, max_output_encoded_bytes=3200)):
            offset, collected = 0, []
            while True:
                value = page(full, offset, 30)
                data = project(value, limits=limits)
                output = json.loads(data)
                cov = output['coverage']
                self.assertLessEqual(len(data), limits.max_output_encoded_bytes)
                self.assertGreater(cov['returned_record_count'], 0)
                self.assertEqual(cov['output_page_end_offset'], offset + len(output['records']))
                collected.extend(output['records'])
                if cov['next_record_offset'] is None:
                    break
                self.assertEqual(cov['next_record_offset'], offset + len(output['records']))
                self.assertLess(cov['next_record_offset'], cov['source_page_end_offset'])
                offset = cov['next_record_offset']
            expected = json.loads(v1.project_replay_feedback(encode(full), submission_id=SUBMISSION,
                job_id=JOB, own_team_id=1, expected_spec_version='2.0.3'))['records']
            self.assertEqual(collected, expected)
            self.assertTrue(any(row['kind'] == 'death' for row in collected))

    def test_late_adverse_rows_beyond_10000_are_retrievable(self):
        full = long_fixture()
        offset, collected = 0, []
        while True:
            output = json.loads(project(page(full, offset, 333), limits=v2.FeedbackLimits(max_records=128)))
            collected.extend(output['records'])
            following = output['coverage']['next_record_offset']
            if following is None:
                break
            self.assertGreater(following, offset)
            offset = following
        self.assertEqual(len(collected), 12053)
        self.assertEqual([(r['kind'], r['row_index']) for r in collected],
                         [(r['kind'], r['row_index']) for r in full['records']])
        self.assertEqual(collected[-2]['action'], 'DIE_EXCEPTION')
        self.assertEqual(collected[-1]['kind'], 'death')
        self.assertLess(collected[10002]['raw_target'], 0)

    def test_late_page_keeps_whole_window_coverage(self):
        output = json.loads(project(page(offset=25, limit=1)))
        cov = output['coverage']
        self.assertFalse(cov['has_more'])
        self.assertFalse(cov['selected_records_complete'])
        self.assertTrue(cov['supplied_page_records_complete'])
        self.assertEqual(cov['truncation_reasons'], ['source_record_offset'])
        self.assertEqual(cov['matching_record_count'], 26)
        self.assertEqual(output['matches'][0]['selected_round_events'], 6)

    def test_empty_and_exact_eof_distinguished(self):
        empty = json.loads(project(page(start=46, end=47)))
        self.assertEqual(empty['records'], [])
        self.assertTrue(empty['coverage']['selected_records_complete'])
        self.assertFalse(empty['matches'][0]['all_requested_positive_rounds_present'])
        eof = json.loads(project(page(offset=26)))
        self.assertFalse(eof['coverage']['selected_records_complete'])
        self.assertIsNone(eof['coverage']['next_record_offset'])
        self.assertEqual(eof['coverage']['output_page_end_offset'], 26)

    def test_zero_progress_budget_rejects(self):
        value = page(limit=2)
        normal = json.loads(project(value))
        normal['records'] = []
        # A budget that could fit coverage alone still cannot issue zero progress.
        self.reject(value, 'output_limit', limits=v2.FeedbackLimits(max_output_encoded_bytes=1600))
        self.reject(value, 'output_limit', limits=v2.FeedbackLimits(max_output_encoded_bytes=1))

    def test_longest_whole_row_prefix_and_progress_receipt(self):
        value = page(limit=26)
        limits = v2.FeedbackLimits(max_output_encoded_bytes=3000)
        output = json.loads(project(value, limits=limits))
        n = len(output['records'])
        self.assertGreater(n, 0)
        self.assertLess(n, 26)
        self.assertEqual(output['coverage']['next_record_offset'], n)
        self.assertEqual(output['coverage']['source_page_end_offset'], 26)
        self.assertIn('projection_output_limit', output['coverage']['truncation_reasons'])
        # Adding the next complete row cannot fit even with shorter reason text.
        output['records'].append(value['records'][n])
        output['records'][-1]['team_slot'] = 'own' if output['records'][-1]['team_id'] == 1 else 'opponent'
        self.assertGreater(len(encode(output)), limits.max_output_encoded_bytes)

    def test_spec_is_explicit_and_exact(self):
        for version in ('2.0.3', '3.0.13'):
            value = page()
            value['spec_version'] = version
            self.assertEqual(json.loads(project(value, expected_spec_version=version))['spec_version'], version)
            self.reject(value, expected_spec_version='3.0.13' if version == '2.0.3' else '2.0.3')
        self.reject(page(), 'invalid_context', expected_spec_version='9.9.9')

    def test_expected_query_cannot_be_overridden(self):
        value = page()
        for field, changed in (('round_start', 34), ('round_end', 41), ('robot_id', None),
                               ('record_offset', 1), ('record_limit', 99)):
            args = dict(vars(query(value)))
            args[field] = changed
            self.reject(value, expected_query=v2.ReplayPageQuery(**args))

    def test_bad_query_construction_and_context(self):
        for field, value in (('round_start', -1), ('round_end', 300), ('record_offset', -1),
                             ('record_offset', 5000001), ('record_limit', 10001),
                             ('robot_id', True), ('record_offset', False), ('record_limit', 0)):
            args = vars(query(page())).copy()
            args[field] = value
            with self.assertRaises(ValueError):
                v2.ReplayPageQuery(**args)
        self.reject(page(), 'invalid_context', expected_query={})

    def test_exact_source_page_arithmetic_required(self):
        for field, value in (('returned_record_count', 1), ('matching_record_count', 25),
                             ('page_end_offset', 0), ('next_record_offset', 0),
                             ('page_records_complete', False), ('truncated', True),
                             ('selected_records_complete', False)):
            sample = page()
            sample[field] = value
            self.reject(sample)
        self.reject(page(offset=27))
        sample = page(offset=3, limit=5)
        sample['next_record_offset'] = None
        self.reject(sample)

    def test_complete_source_required_even_for_empty_page(self):
        for field, changed in (('status', 'incomplete'), ('source_stream_complete', False),
                               ('extracted_fields_valid', False), ('all_schema_fields_validated', True)):
            value = page(offset=26)
            value[field] = changed
            self.reject(value)

    def test_v1_inputs_not_implicitly_upgraded(self):
        value = page()
        value['format'] = v1.QUERY_FORMAT
        self.reject(value)
        with self.assertRaises(v1.FeedbackRejected):
            v1.project_replay_feedback(encode(page()), submission_id=SUBMISSION, job_id=JOB,
                                      own_team_id=1, expected_spec_version='2.0.3')

    def test_nested_metadata_and_string_injection_dropped(self):
        value = page(offset=2, limit=10)
        before = project(value)
        payload = {'nested': [SECRET, {'schema': SECRET, 'next_record_offset': SECRET}]}
        for obj in (value, value['query'], value['matches'][0], *value['records']):
            obj['unknown_extension'] = payload
            obj['raw_log'] = SECRET
            obj['submission_id'] = SECRET
        self.assertEqual(project(value), before)
        self.assertNotIn(SECRET.encode(), before)
        for field in (b'source_replay', b'sha256', b'map', b'raw_log'):
            self.assertNotIn(field, before)

    def test_bad_rows_past_local_cap_reject(self):
        for field, bad in (('kind', 'unknown'), ('action', SECRET), ('action_code', 16),
                           ('raw_target', 2147483648), ('team_id', True)):
            value = page()
            row = next(row for row in reversed(value['records']) if row['kind'] == 'action')
            row[field] = bad
            self.reject(value, limits=v2.FeedbackLimits(max_records=1))

    def test_duplicate_json_keys_and_depth_limits(self):
        args = dict(submission_id=SUBMISSION, job_id=JOB, own_team_id=1,
                    expected_spec_version='2.0.3', expected_query=query(page()))
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1.0}',
                     b'[' * 33 + b'0' + b']' * 33, b'\xff'):
            with self.assertRaises(v2.FeedbackRejected):
                v2.project_replay_feedback_page(data, **args)
        self.reject(page(), 'input_limit', limits=v2.FeedbackLimits(max_input_encoded_bytes=1))
        self.reject(page(), 'query_limit', limits=v2.FeedbackLimits(max_round_span=2))

    def test_resource_row1_only_allowed_at_true_page_boundary(self):
        # Offset2 begins at team1 row1, which legitimately follows an unseen row0.
        self.assertEqual(json.loads(project(page(offset=2, limit=1)))['records'][0]['row_index'], 1)
        value = page(limit=2)
        value['records'][1]['row_index'] = 1
        self.reject(value)

    def test_visible_complete_round_cannot_hide_resource_row(self):
        full = fixture(matches=2, limit=10000)
        full['matches'][1]['source_resource_records_cover_observed_rounds'] = False
        del full['records'][1]
        full['matching_record_count'] -= 1
        full['matching_counts_by_kind']['team_resource_delta'] -= 1
        self.reject(page(full))
        # A middle-page complete resource group still requires both sides.
        full = fixture(limit=10000)
        target = next(i for i, row in enumerate(full['records'])
                      if row['round'] == 37 and row['kind'] == 'team_resource_delta')
        del full['records'][target]
        full['matching_record_count'] -= 1
        full['matching_counts_by_kind']['team_resource_delta'] -= 1
        full['matches'][0]['source_resource_records_cover_observed_rounds'] = False
        # Partial source-resource coverage allows genuinely missing team rows,
        # but vector row index1 without its visible row0 remains contradictory.
        self.reject(page(full, 4, 20))

    def test_global_resource_lower_bound_survives_hidden_prefix(self):
        value = page(matches=2, offset=50, limit=1)
        value['matches'][1]['source_resource_records_cover_observed_rounds'] = False
        value['matching_counts_by_kind']['team_resource_delta'] = 0
        value['matching_counts_by_kind']['action'] += 24
        self.reject(value)

    def test_visible_rounds_cannot_exceed_supplied_coverage(self):
        full = fixture(limit=10000)
        full['records'] = [row for row in full['records'] if row['kind'] != 'team_resource_delta']
        full['matching_record_count'] = len(full['records'])
        del full['matching_counts_by_kind']['team_resource_delta']
        full['matches'][0].update(observed_round_events=44, source_round_ids_contiguous=False,
            source_round_records_cover_reported_rounds=False,
            source_resource_records_cover_observed_rounds=False, selected_round_events=2,
            all_requested_positive_rounds_present=False)
        self.reject(page(full))

    def test_order_duplicate_resource_and_wrong_identity_reject(self):
        for mutation in ('reverse', 'duplicate_team', 'wrong_identity', 'wrong_filter'):
            value = page()
            if mutation == 'reverse':
                value['records'][0:2] = reversed(value['records'][0:2])
            elif mutation == 'duplicate_team':
                value['records'][2]['team_id'] = value['records'][1]['team_id']
            elif mutation == 'wrong_identity':
                value['records'][0]['body_type'] = 'HEADQUARTERS'
            else:
                value['records'][0]['robot_id'] = 44
            self.reject(value)


if __name__ == '__main__':
    unittest.main()
