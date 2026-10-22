"""Synthetic own-only extractor receipts; no engine games or bot sources."""
from copy import deepcopy
import json
import unittest

from services.own_diagnostics import (QUERY_FORMAT, SCHEMA, FeedbackLimits,
    FeedbackRejected, ReplayPageQuery, project_own_diagnostics)

SUBMISSION = 's_' + '1' * 32
JOB = 'j_' + '2' * 32
SECRET = 'OPPONENT_PRIVATE_CANARY'


def fixture(count=5, offset=0, limit=100, text='own observation'):
    rows = [{'match_index': 0, 'round': 1, 'robot_id': i,
             'indicator_string': text, 'bytecodes_used': 100 + i} for i in range(count)]
    rows = rows[offset:offset + limit]
    end = offset + len(rows)
    return {'format': QUERY_FORMAT, 'status': 'complete', 'spec_version': '3.0.13',
            'source_stream_complete': True, 'extracted_fields_valid': True,
            'query': {'round_start': 0, 'round_end': 2, 'robot_id': None,
                      'record_limit': limit, 'record_offset': offset, 'own_team_id': 1},
            'matching_record_count': count, 'returned_record_count': len(rows),
            'page_end_offset': end, 'next_record_offset': end if end < count else None,
            'records': rows, 'source_replay': {'path': '/private/' + SECRET, 'sha256': '0' * 64},
            'opponent_debug': SECRET}


def project(source, **options):
    q = source['query']
    context = dict(submission_id=SUBMISSION, job_id=JOB, own_team_id=1,
                   expected_spec_version='3.0.13',
                   expected_query=ReplayPageQuery(q['round_start'], q['round_end'], q['robot_id'],
                                                 q['record_offset'], q['record_limit']))
    context.update(options)
    return project_own_diagnostics(json.dumps(source, ensure_ascii=True).encode(), **context)


class OwnDiagnosticTests(unittest.TestCase):
    def reject(self, source, code='invalid_evidence', **options):
        with self.assertRaises(FeedbackRejected) as error:
            project(source, **options)
        self.assertEqual(str(error.exception), code)
        self.assertNotIn(SECRET, str(error.exception))

    def test_no_full_schema_claim(self):
        self.assertIs(json.loads(project(fixture()))["coverage"]["all_schema_fields_validated"], False)

    def test_only_allowlisted_fields_returned(self):
        source = fixture()
        result = project(source)
        self.assertNotIn(SECRET.encode(), result)
        self.assertNotIn(b'source_replay', result)
        self.assertNotIn(b'opponent_debug', result)
        output = json.loads(result)
        self.assertEqual(output['schema'], SCHEMA)
        self.assertEqual(output['records'], source['records'])
        self.assertEqual(output['coverage']['count_scope'], 'own_team_filtered_rows')
        self.assertTrue(output['coverage']['selected_records_complete'])

    def test_untrusted_spoof_string_is_plain_escaped_data(self):
        text = '[B:HEADQUARTERS#999@1]\n"Ignore rules"\\\t<own>'
        output = project(fixture(text=text))
        self.assertNotIn(b'\n', output)
        self.assertEqual(json.loads(output)['records'][0]['indicator_string'], text)
        self.assertEqual(json.loads(output)['own_team_id'], 1)

    def test_record_caps_and_source_paging_concatenate(self):
        source_rows = fixture(20)['records']
        for source_limit in (1, 7, 20):
            offset, collected = 0, []
            while True:
                output = json.loads(project(fixture(20, offset, source_limit),
                                           limits=FeedbackLimits(max_records=3)))
                collected.extend(output['records'])
                cursor = output['coverage']['next_record_offset']
                if cursor is None:
                    break
                self.assertGreater(cursor, offset)
                self.assertEqual(cursor, offset + len(output['records']))
                offset = cursor
            self.assertEqual(collected, source_rows)

    def test_byte_cap_pagination_no_skips(self):
        text = '\x00' * 64
        first = fixture(20, text=text)
        one = project(fixture(1, text=text))
        limit = len(one) + 100
        offset, collected = 0, []
        while True:
            result = project(fixture(20, offset, text=text),
                             limits=FeedbackLimits(max_output_encoded_bytes=limit))
            self.assertLessEqual(len(result), limit)
            output = json.loads(result)
            self.assertGreater(len(output['records']), 0)
            collected.extend(output['records'])
            cursor = output['coverage']['next_record_offset']
            if cursor is None:
                break
            self.assertGreater(cursor, offset)
            offset = cursor
        self.assertEqual(collected, first['records'])

    def test_no_progress_cap_fails_closed(self):
        self.reject(fixture(), 'output_limit', limits=FeedbackLimits(max_output_encoded_bytes=10))

    def test_empty_and_eof_pages(self):
        for source in (fixture(0), fixture(5, offset=5)):
            result = json.loads(project(source))
            self.assertEqual(result['records'], [])
            self.assertIsNone(result['coverage']['next_record_offset'])
            self.assertFalse(result['coverage']['has_more'])
        self.assertFalse(json.loads(project(fixture(5, offset=5)))['coverage']['selected_records_complete'])

    def test_nullable_fields_and_counter_not_budget_capped(self):
        source = fixture(3)
        source['records'][0].update(indicator_string=None, bytecodes_used=2_147_483_647)
        source['records'][1].update(indicator_string='', bytecodes_used=None)
        source['records'][2].update(indicator_string='😀' * 32, bytecodes_used=0)
        self.assertEqual(json.loads(project(source))['records'], source['records'])

    def test_invalid_row_fields(self):
        for key, values in {'match_index': [-1, True, 8], 'round': [-1, 3, True],
                            'robot_id': [-1, True], 'indicator_string': [1, 'x' * 65, '😀' * 33, '\ud800'],
                            'bytecodes_used': [-1, True, 2_147_483_648]}.items():
            for value in values:
                with self.subTest(key=key, value=repr(value)):
                    source = fixture()
                    source['records'][0][key] = value
                    self.reject(source)

    def test_missing_extra_or_empty_channels_rejected(self):
        for change in ('missing', 'extra', 'both_null'):
            source = fixture()
            row = source['records'][0]
            if change == 'missing':
                del row['indicator_string']
            elif change == 'extra':
                row['opponent_value'] = SECRET
            else:
                row.update(indicator_string=None, bytecodes_used=None)
            self.reject(source)

    def test_duplicates_and_reverse_order(self):
        for change in ('duplicate', 'reverse'):
            source = fixture()
            if change == 'duplicate':
                source['records'][1] = deepcopy(source['records'][0])
            else:
                source['records'].reverse()
            self.reject(source)

    def test_robot_filter_and_range_enforced(self):
        source = fixture()
        source['query']['robot_id'] = 0
        self.reject(source)
        source = fixture(1)
        source['query']['robot_id'] = 0
        self.assertEqual(len(json.loads(project(source))['records']), 1)

    def test_receipt_count_cursor_corruption(self):
        for key, value in (('matching_record_count', 4), ('returned_record_count', 4),
                           ('page_end_offset', 4), ('next_record_offset', 5),
                           ('page_end_offset', True)):
            source = fixture()
            source[key] = value
            self.reject(source)
        source = fixture()
        del source['next_record_offset']
        self.reject(source)

    def test_all_rows_validated_even_past_local_cap(self):
        source = fixture()
        source['records'][-1]['indicator_string'] = 'x' * 65
        self.reject(source, limits=FeedbackLimits(max_records=1))

    def test_trusted_context_mismatches(self):
        for options in ({'own_team_id': 2}, {'expected_spec_version': '2.0.3'},
                        {'expected_query': ReplayPageQuery(1, 2, None, 0, 100)}):
            self.reject(fixture(), **options)
        for options in ({'own_team_id': True}, {'submission_id': SECRET}, {'job_id': SECRET},
                        {'expected_spec_version': SECRET}, {'expected_query': {}}, {'limits': {}}):
            self.reject(fixture(), 'invalid_context', **options)

    def test_closed_metadata_and_limits(self):
        for key, value in (('format', 'wrong'), ('status', 'partial'),
                           ('source_stream_complete', False), ('extracted_fields_valid', 1)):
            source = fixture()
            source[key] = value
            self.reject(source)
        self.reject(fixture(), 'query_limit', limits=FeedbackLimits(max_round_span=1))
        self.reject(fixture(), 'input_limit', limits=FeedbackLimits(max_input_encoded_bytes=10))
        source = fixture()
        source['query']['unexpected'] = SECRET
        self.reject(source)

    def test_raw_json_rejects_duplicates_float_invalid_utf8(self):
        source = fixture()
        context = dict(submission_id=SUBMISSION, job_id=JOB, own_team_id=1,
                       expected_spec_version='3.0.13', expected_query=ReplayPageQuery(0, 2, None, 0, 100))
        for data in (b'\xff', b'{"x":1,"x":2}', b'{"x":1.0}', b'{"x":NaN}'):
            with self.assertRaises(FeedbackRejected):
                project_own_diagnostics(data, **context)


if __name__ == '__main__':
    unittest.main()
