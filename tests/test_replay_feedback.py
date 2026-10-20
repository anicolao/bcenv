"""Synthetic ReplayQuery projection tests; no engine, heldouts or listeners.

fixture() follows ReplayQueryTest.java's 45-round official-schema builder:
robot 42 starts as HQ, robot 43 (opponent carrier) spawns at 35, dies at 40;
unknown robot 999 dies at 41. Rows and enum values below are independently
specified from ReplayQuery.java and the pinned schema, not copied from the
projector's enum constants. This does not prove real-game parser validity.
"""
from collections import Counter
from copy import deepcopy
import json
import unittest

from services import replay_feedback as feedback


SUBMISSION = 's_' + 'a' * 32
JOB = 'j_' + 'b' * 32
SECRET = 'PRIVATE_opponent.package_/private/path_map_name_hash_raw_log_schedule'


def encode(value):
    return json.dumps(value, separators=(',', ':')).encode('utf-8')


def fixture(start=35, end=40, robot=43, limit=100, matches=1, gap=False):
    rows, coverage = [], []
    for match in range(matches):
        first_team = 1 if match == 0 else 2
        first_type, first_name = (0, 'HEADQUARTERS') if match == 0 else (2, 'LAUNCHER')
        event_base = match * (46 if gap else 47)

        def add(kind, round_id, index, robot_id=None, team_id=None,
                body_code=None, body_name=None, observed=None, **fields):
            if not start <= round_id <= end:
                return
            if robot_id is not None and robot is not None and robot_id != robot:
                return
            event_index = event_base + round_id + 1 - int(gap and round_id >= 35)
            row = dict(kind=kind, match_index=match, event_index=event_index,
                       round=round_id, row_index=index)
            if robot_id is not None:
                row.update(robot_id=robot_id, team_id=team_id, body_type_code=body_code,
                           body_type=body_name, identity_observed_round=observed)
            elif team_id is not None:
                row['team_id'] = team_id
            row.update(fields)
            rows.append(row)

        add('initial_body', 0, 0, 42, first_team, first_type, first_name, 0, x=5, y=6)
        for round_id in range(1, 46):
            if gap and round_id == 34:
                continue
            if round_id == 35:
                add('spawn', round_id, 0, 43, 2, 1, 'CARRIER', 35, x=7, y=8)
            add('team_resource_delta', round_id, 0, team_id=2, adamantium_delta=-round_id,
                mana_delta=0, elixir_delta=1)
            add('team_resource_delta', round_id, 1, team_id=1, adamantium_delta=round_id,
                mana_delta=2, elixir_delta=0)
            add('movement', round_id, 0, 42, first_team, first_type, first_name, 0,
                x=round_id, y=round_id + 2)
            if 35 <= round_id <= 40:
                add('movement', round_id, 1, 43, 2, 1, 'CARRIER', 35,
                    x=round_id + 1, y=round_id + 3)
            add('action', round_id, 0, 42, first_team, first_type, first_name, 0,
                action_code=12, action='CHANGE_ADAMANTIUM', raw_target=-round_id)
            if 35 <= round_id <= 40:
                add('action', round_id, 1, 43, 2, 1, 'CARRIER', 35,
                    action_code=3, action='PICK_UP_RESOURCE', raw_target=9000 + round_id)
            if round_id == 40:
                add('death', round_id, 0, 43, 2, 1, 'CARRIER', 35)
            if round_id == 41:
                add('death', round_id, 0, 999)
        selected = [r for r in range(max(1, start), min(45, end) + 1)
                    if not (gap and r == 34)]
        coverage.append(dict(match_index=match, map=SECRET,
            observed_round_events=44 if gap else 45, last_observed_round=45,
            reported_total_rounds=45, match_footer_present=True,
            source_round_ids_contiguous=not gap,
            source_round_records_cover_reported_rounds=not gap,
            source_resource_records_cover_observed_rounds=True,
            selected_round_events=len(selected), first_selected_round=selected[0] if selected else None,
            last_selected_round=selected[-1] if selected else None,
            all_requested_positive_rounds_present=len(selected) == max(0, end - max(1, start) + 1)))
    count = len(rows)
    return dict(format='bcenv.bc2023.replay-query.v1', status='complete', spec_version='2.0.3',
        extracted_fields_valid=True, all_schema_fields_validated=False, source_stream_complete=True,
        query=dict(round_start=start, round_end=end, round_bounds_inclusive=True, robot_id=robot,
                   record_limit=limit, team_resource_rows_included_with_robot_filter=True,
                   match_scope='all matches, separately indexed'),
        matching_record_count=count, matching_counts_by_kind=dict(Counter(row['kind'] for row in rows)),
        returned_record_count=min(count, limit), truncated=count > limit,
        selected_records_complete=count <= limit, matches=coverage, records=rows[:limit],
        source_replay=dict(path=SECRET, sha256=SECRET, compressed_bytes=123, uncompressed_bytes=456),
        limits=dict(max_returned_records=10000), semantics=[SECRET])


def project(source, *, expected_spec_version='2.0.3', **kwargs):
    return json.loads(feedback.project_replay_feedback(
        encode(source), submission_id=SUBMISSION, job_id=JOB, own_team_id=1,
        expected_spec_version=expected_spec_version, **kwargs))


class ReplayFeedbackTests(unittest.TestCase):
    def assert_rejected(self, source, code='invalid_evidence', **kwargs):
        with self.assertRaises(feedback.FeedbackRejected) as caught:
            project(source, **kwargs)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn(SECRET, str(caught.exception))

    def test_source_derived_semantic_fixture(self):
        out = project(fixture())
        self.assertEqual(out['coverage']['matching_record_count'], 26)
        self.assertEqual(out['coverage']['returned_record_count'], 26)
        self.assertTrue(out['coverage']['selected_records_complete'])
        self.assertFalse(out['coverage']['all_schema_fields_validated'])
        self.assertEqual(out['records'][0], dict(kind='spawn', match_index=0, event_index=36,
            round=35, row_index=0, robot_id=43, team_id=2, team_slot='opponent',
            body_type_code=1, body_type='CARRIER', identity_observed_round=35, x=7, y=8))
        self.assertEqual(out['records'][1]['adamantium_delta'], -35)
        self.assertEqual(out['records'][1]['team_slot'], 'opponent')
        self.assertEqual(out['records'][2]['team_slot'], 'own')
        self.assertEqual(out['records'][3]['x'], 36)
        self.assertEqual(out['records'][4]['raw_target'], 9035)
        self.assertEqual(out['records'][-1]['kind'], 'death')
        self.assertNotIn('cause', out['records'][-1])
        self.assertNotIn('x', out['records'][-1])

    def test_all_six_kinds_retained_without_side_or_outcome_filtering(self):
        out = project(fixture(start=0, end=45, robot=None, limit=1000))
        self.assertEqual(set(row['kind'] for row in out['records']), set(feedback.KINDS))
        self.assertEqual(set(row['team_slot'] for row in out['records']), {'own', 'opponent', None})
        self.assertEqual([row['robot_id'] for row in out['records'] if row['kind'] == 'death'], [43, 999])
        self.assertTrue(any(row.get('raw_target', 0) < 0 for row in out['records']))

    def test_unknown_identity_is_explicit_and_never_invented(self):
        out = project(fixture(start=41, end=41, robot=999))
        self.assertEqual(len(out['records']), 3)  # Both resource rows survive robot filter.
        for field in ('team_id', 'team_slot', 'body_type_code', 'body_type', 'identity_observed_round'):
            self.assertIsNone(out['records'][-1][field])

    def test_team_mapping_only_comes_from_trusted_host(self):
        source = fixture()
        source.update(own_team_id=2, submission_id=SECRET, job_id=SECRET)
        out = project(source)
        self.assertEqual((out['submission_id'], out['job_id']), (SUBMISSION, JOB))
        self.assertEqual(out['records'][0]['team_slot'], 'opponent')
        swapped = json.loads(feedback.project_replay_feedback(encode(source),
            submission_id=SUBMISSION, job_id=JOB, own_team_id=2, expected_spec_version='2.0.3'))
        self.assertEqual(swapped['records'][0]['team_slot'], 'own')

    def test_initial_zero_round_and_match_local_identity(self):
        out = project(fixture(start=0, end=0, robot=42, matches=2))
        self.assertEqual([row['body_type'] for row in out['records']], ['HEADQUARTERS', 'LAUNCHER'])
        self.assertEqual([row['match_index'] for row in out['records']], [0, 1])
        self.assertEqual([row['event_index'] for row in out['records']], [1, 48])
        self.assertTrue(all(match['all_requested_positive_rounds_present'] for match in out['matches']))
        self.assertEqual([match['selected_round_events'] for match in out['matches']], [0, 0])

    def test_source_prefix_truncation_is_accepted_and_honest(self):
        complete = project(fixture())
        out = project(fixture(limit=3))
        coverage = out['coverage']
        self.assertEqual(out['records'], complete['records'][:3])
        self.assertEqual(coverage['matching_record_count'], 26)
        self.assertEqual(coverage['matching_counts_by_kind']['death'], 1)
        self.assertTrue(coverage['source_truncated'])
        self.assertFalse(coverage['projection_truncated'])
        self.assertFalse(coverage['selected_records_complete'])
        self.assertTrue(out['matches'][0]['all_requested_positive_rounds_present'])
        self.assertEqual(coverage['truncation_reasons'], ['source_record_limit'])

    def test_projection_record_cap_keeps_exact_prefix(self):
        out = project(fixture(), limits=feedback.FeedbackLimits(max_records=3))
        self.assertEqual(out['records'], project(fixture())['records'][:3])
        self.assertEqual(out['coverage']['source_returned_record_count'], 26)
        self.assertEqual(out['coverage']['truncation_reasons'], ['projection_record_limit'])
        self.assertFalse(out['coverage']['source_truncated'])
        self.assertTrue(out['coverage']['projection_truncated'])

    def test_exact_source_and_projection_limits_are_not_truncation(self):
        out = project(fixture(limit=26), limits=feedback.FeedbackLimits(max_records=26))
        self.assertTrue(out['coverage']['selected_records_complete'])
        self.assertFalse(out['coverage']['truncated'])
        self.assertEqual(out['coverage']['truncation_reasons'], [])

    def test_byte_cap_is_measured_after_final_json_encoding(self):
        source = fixture()
        result = feedback.project_replay_feedback(encode(source), submission_id=SUBMISSION,
            job_id=JOB, own_team_id=1, expected_spec_version='2.0.3',
            limits=feedback.FeedbackLimits(max_output_encoded_bytes=2500))
        out = json.loads(result)
        self.assertLessEqual(len(result), 2500)
        self.assertGreater(len(out['records']), 0)
        self.assertLess(len(out['records']), 26)
        self.assertEqual(out['records'], project(source)['records'][:len(out['records'])])
        self.assertEqual(out['coverage']['truncation_reasons'], ['projection_output_limit'])
        self.assertEqual(result, json.dumps(out, sort_keys=True, separators=(',', ':')).encode())

    def test_byte_cap_keeps_longest_fitting_prefix_including_zero(self):
        source = fixture()
        complete = project(source)
        counts_seen = set()
        for cap in range(1400, 10500, 173):
            expected = None
            # Independently encode every candidate prefix with its final flags.
            for count in range(27):
                candidate = deepcopy(complete)
                candidate['records'] = candidate['records'][:count]
                candidate['limits']['max_output_encoded_bytes'] = cap
                candidate['coverage'].update(returned_record_count=count,
                    projection_truncated=count < 26, truncated=count < 26,
                    selected_records_complete=count == 26,
                    truncation_reasons=['projection_output_limit'] if count < 26 else [])
                if len(encode(candidate)) <= cap:
                    expected = candidate
            limits = feedback.FeedbackLimits(max_output_encoded_bytes=cap)
            if expected is None:
                self.assert_rejected(source, 'output_limit', limits=limits)
            else:
                actual = project(source, limits=limits)
                self.assertEqual(actual, expected)
                counts_seen.add(len(actual['records']))
        self.assertIn(0, counts_seen)
        self.assertIn(26, counts_seen)

    def test_multiple_caps_are_not_conflated(self):
        out = project(fixture(limit=20), limits=feedback.FeedbackLimits(
            max_records=10, max_output_encoded_bytes=2500))
        self.assertEqual(out['coverage']['truncation_reasons'],
                         ['source_record_limit', 'projection_record_limit', 'projection_output_limit'])

    def test_header_cannot_fit_fails_without_partial_metrics(self):
        self.assert_rejected(fixture(), 'output_limit',
                             limits=feedback.FeedbackLimits(max_output_encoded_bytes=1))

    def test_empty_selection_does_not_claim_requested_rounds_exist(self):
        out = project(fixture(start=46, end=47, robot=None))
        self.assertEqual(out['records'], [])
        self.assertTrue(out['coverage']['selected_records_complete'])
        self.assertFalse(out['matches'][0]['all_requested_positive_rounds_present'])
        self.assertTrue(out['coverage']['source_stream_complete'])

    def test_round_gaps_are_exposed_without_calling_stream_incomplete(self):
        out = project(fixture(start=33, end=35, robot=None, gap=True))
        self.assertTrue(out['coverage']['source_stream_complete'])
        self.assertTrue(out['coverage']['selected_records_complete'])
        self.assertFalse(out['matches'][0]['source_round_ids_contiguous'])
        self.assertFalse(out['matches'][0]['all_requested_positive_rounds_present'])
        self.assertEqual(out['matches'][0]['selected_round_events'], 2)

    def test_private_metadata_and_unknown_nested_extensions_never_escape(self):
        source = fixture(start=0, end=45, robot=None, limit=1000)
        baseline = project(source)
        extension = {SECRET: [SECRET, {'nested': SECRET, 'url': 'https://example.invalid/' + SECRET}]}
        source.update(teams=extension, opponent=extension, package=SECRET,
                      source_hash=SECRET, binary_hash=SECRET, raw_logs=extension,
                      hidden_schedule=extension, future_extension=extension)
        source['query']['future_extension'] = extension
        source['matching_counts_by_kind']['future_extension'] = extension
        for match in source['matches']:
            match.update(teams=extension, package=SECRET, source_hash=SECRET, schedule=extension)
        for row in source['records']:
            row.update(name=SECRET, package=SECRET, path=SECRET, source_hash=SECRET,
                       binary_hash=SECRET, raw_log=SECRET, map=SECRET, schedule=extension,
                       metadata=extension, future_extension=extension, winner=SECRET)
        out = project(source)
        self.assertEqual(out, baseline)
        self.assertNotIn(SECRET, json.dumps(out))

    def test_kind_specific_allowlists_do_not_copy_other_kinds_fields(self):
        source = fixture()
        source['records'][0].update(action=SECRET, raw_target=SECRET, adamantium_delta=SECRET)
        source['records'][1].update(robot_id=SECRET, body_type=SECRET, x=SECRET)
        source['records'][-1].update(x=SECRET, y=SECRET, raw_target=SECRET, cause=SECRET)
        out = project(source)
        self.assertEqual(out, project(fixture()))

    def test_supported_string_fields_reject_injected_values(self):
        for path in [('format',), ('status',), ('spec_version',), ('query', 'match_scope'),
                     ('records', 0, 'kind'), ('records', 0, 'body_type'), ('records', 4, 'action')]:
            for value in (SECRET, {'metadata': SECRET}, [SECRET], None, True, 1):
                with self.subTest(path=path, value=value):
                    source = fixture()
                    parent = source
                    for key in path[:-1]:
                        parent = parent[key]
                    parent[path[-1]] = value
                    self.assert_rejected(source)

    def test_incomplete_invalid_or_unsupported_evidence_fails_closed(self):
        mutations = [('status', 'incomplete'), ('status', 'invalid'), ('status', 'timeout'),
                     ('source_stream_complete', False), ('extracted_fields_valid', False),
                     ('all_schema_fields_validated', True), ('spec_version', '3.0.15'),
                     ('format', 'bcenv.bc2023.replay-summary.v1')]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                source = fixture()
                source[field] = value
                self.assert_rejected(source)
        source = fixture()
        source['matches'][0]['match_footer_present'] = False
        self.assert_rejected(source)

    def test_corrupt_tail_after_local_cap_still_rejects(self):
        for field, value in [('kind', 'future_record'), ('team_id', 3), ('robot_id', SECRET),
                             ('identity_observed_round', 41)]:
            source = fixture()
            source['records'][-1][field] = value
            self.assert_rejected(source, limits=feedback.FeedbackLimits(max_records=1))

    def test_closed_action_and_body_enums(self):
        names = ['LAUNCH_ATTACK', 'THROW_ATTACK', 'SPAWN_UNIT', 'PICK_UP_RESOURCE',
                 'PLACE_RESOURCE', 'DESTABILIZE', 'DESTABILIZE_DAMAGE', 'BOOST',
                 'BUILD_ANCHOR', 'PICK_UP_ANCHOR', 'PLACE_ANCHOR', 'CHANGE_HEALTH',
                 'CHANGE_ADAMANTIUM', 'CHANGE_MANA', 'CHANGE_ELIXIR', 'DIE_EXCEPTION']
        for code, name in enumerate(names):
            source = fixture()
            source['records'][4].update(action_code=code, action=name)
            self.assertEqual(project(source)['records'][4]['action'], name)
        for code, name in enumerate(['HEADQUARTERS', 'CARRIER', 'LAUNCHER',
                                      'AMPLIFIER', 'DESTABILIZER', 'BOOSTER']):
            source = fixture()
            source['records'][0].update(body_type_code=code, body_type=name)
            self.assertEqual(project(source)['records'][0]['body_type'], name)
        for code in (-1, 16, True, '12', {'code': 12}):
            source = fixture()
            source['records'][4]['action_code'] = code
            self.assert_rejected(source)
        source = fixture()
        source['records'][4]['action'] = 'LAUNCH_ATTACK'  # valid name, wrong code
        self.assert_rejected(source)

    def test_signed_schema_int32_values_preserved_without_decoding(self):
        source = fixture()
        source['records'][0].update(x=-2147483648, y=2147483647)
        source['records'][1].update(adamantium_delta=-2147483648, mana_delta=2147483647)
        source['records'][4]['raw_target'] = -2147483648
        out = project(source)
        self.assertEqual(out['records'][0]['x'], -2147483648)
        self.assertEqual(out['records'][1]['mana_delta'], 2147483647)
        self.assertEqual(out['records'][4]['raw_target'], -2147483648)

    def test_numeric_types_and_bounds_are_strict(self):
        paths = [('records', 0, 'robot_id'), ('records', 0, 'x'),
                 ('records', 1, 'adamantium_delta'), ('records', 4, 'raw_target'),
                 ('query', 'round_start'), ('matching_record_count',),
                 ('matches', 0, 'observed_round_events')]
        for path in paths:
            for value in (True, False, 1.0, '1', None, {}, [], 2147483648, -2147483649):
                with self.subTest(path=path, value=value):
                    source = fixture()
                    parent = source
                    for key in path[:-1]:
                        parent = parent[key]
                    parent[path[-1]] = value
                    self.assert_rejected(source)

    def test_missing_required_fields_and_identity_consistency(self):
        for path in [('query', 'robot_id'), ('records', 0, 'team_id'),
                     ('records', 0, 'body_type'), ('records', 0, 'x'),
                     ('records', 4, 'action'), ('records', 1, 'mana_delta'),
                     ('matches', 0, 'first_selected_round')]:
            source = fixture()
            parent = source
            for key in path[:-1]:
                parent = parent[key]
            del parent[path[-1]]
            self.assert_rejected(source)
        source = fixture()
        source['records'][0]['team_id'] = None
        self.assert_rejected(source)
        source = fixture(start=41, end=41, robot=999)
        source['records'][-1]['body_type_code'] = 0
        self.assert_rejected(source)

    def test_counts_and_coverage_cannot_contradict_the_prefix(self):
        cases = [lambda s: s.update(returned_record_count=25),
                 lambda s: s.update(matching_record_count=27),
                 lambda s: s.update(truncated=True),
                 lambda s: s.update(selected_records_complete=False),
                 lambda s: s['matching_counts_by_kind'].update(death=2),
                 lambda s: s['matching_counts_by_kind'].update(death=0, spawn=2),
                 lambda s: s['matches'][0].update(selected_round_events=5),
                 lambda s: s['matches'][0].update(first_selected_round=34),
                 lambda s: s['matches'][0].update(all_requested_positive_rounds_present=False),
                 lambda s: s['matches'][0].update(last_observed_round=46),
                 lambda s: s['matches'][0].update(source_round_records_cover_reported_rounds=False)]
        for change in cases:
            source = fixture()
            change(source)
            self.assert_rejected(source)

    def test_invalid_order_round_filter_and_event_context(self):
        cases = [lambda s: s['records'].reverse(),
                 lambda s: s['records'][1].update(row_index=1),
                 lambda s: s['records'][3].update(round=36),
                 lambda s: s['records'][3].update(event_index=37),
                 lambda s: s['records'][3].update(match_index=1),
                 lambda s: s['records'][3].update(robot_id=42),
                 lambda s: s['records'][0].update(kind='initial_body'),
                 lambda s: s['records'][0].update(identity_observed_round=34)]
        for change in cases:
            source = fixture()
            change(source)
            self.assert_rejected(source)

    def test_duplicate_or_excess_resource_team_rows_rejected(self):
        source = fixture()
        source['records'][2]['team_id'] = 2
        self.assert_rejected(source)
        source = fixture()
        source['records'][2]['row_index'] = 2
        self.assert_rejected(source)

    def test_resource_coverage_not_reconstructed_or_overclaimed(self):
        source = fixture()
        source['matches'][0]['source_resource_records_cover_observed_rounds'] = False
        out = project(source)
        self.assertFalse(out['matches'][0]['source_resource_records_cover_observed_rounds'])
        # Missing a row is allowed only when source coverage admits it, with
        # matching/returned counts that actually reflect the missing evidence.
        source['records'].pop(1)
        source['records'][1]['row_index'] = 0
        source['matching_counts_by_kind']['team_resource_delta'] -= 1
        source['matching_record_count'] -= 1
        source['returned_record_count'] -= 1
        self.assertEqual(len(project(source)['records']), 25)
        source['matches'][0]['source_resource_records_cover_observed_rounds'] = True
        self.assert_rejected(source)

    def test_resource_completeness_is_checked_for_each_match(self):
        source = fixture(matches=2)
        source['matches'][1]['source_resource_records_cover_observed_rounds'] = False
        source['records'].pop(2)  # Match 0's second team resource row at round 35.
        source['matching_record_count'] -= 1
        source['returned_record_count'] -= 1
        source['matching_counts_by_kind']['team_resource_delta'] -= 1
        self.assert_rejected(source)

    def test_mixed_resource_coverage_sets_a_lower_bound_beyond_source_cap(self):
        source = fixture(matches=2, limit=1)
        source['matches'][1]['source_resource_records_cover_observed_rounds'] = False
        # The one retained row is a birth, but all match 0 resource rows must
        # still contribute to the complete source query's matching counts.
        removed = source['matching_counts_by_kind'].pop('team_resource_delta')
        source['matching_record_count'] -= removed
        self.assert_rejected(source)

    def test_truncated_prefix_checks_complete_rounds_and_prior_matches(self):
        source = fixture(matches=2, limit=30)
        source['matches'][1]['source_resource_records_cover_observed_rounds'] = False
        source['records'].pop(2)
        source['query']['record_limit'] -= 1
        source['returned_record_count'] -= 1
        self.assert_rejected(source)
        source = fixture(limit=20)
        source['records'].pop(2)
        source['query']['record_limit'] -= 1
        source['returned_record_count'] -= 1
        self.assert_rejected(source)

    def test_cap_inside_any_last_group_is_supported(self):
        for limit in range(1, 27):
            with self.subTest(limit=limit):
                out = project(fixture(limit=limit))
                self.assertEqual(out['coverage']['returned_record_count'], limit)
        out = project(fixture(start=0, end=0, robot=42, matches=2, gap=True))
        self.assertEqual([row['event_index'] for row in out['records']], [1, 47])

    def test_selected_round_count_cannot_be_less_than_visible_rounds(self):
        source = fixture()
        source['records'] = [row for row in source['records'] if row['kind'] != 'team_resource_delta']
        source['matching_record_count'] = source['returned_record_count'] = len(source['records'])
        del source['matching_counts_by_kind']['team_resource_delta']
        source['matches'][0].update(observed_round_events=44,
            source_round_ids_contiguous=False, source_round_records_cover_reported_rounds=False,
            source_resource_records_cover_observed_rounds=False, selected_round_events=2,
            all_requested_positive_rounds_present=False)
        self.assert_rejected(source)

    def test_input_byte_budget_before_parsing(self):
        source = fixture()
        size = len(encode(source))
        project(source, limits=feedback.FeedbackLimits(max_input_encoded_bytes=size))
        self.assert_rejected(source, 'input_limit',
                             limits=feedback.FeedbackLimits(max_input_encoded_bytes=size - 1))

    def test_range_match_and_input_record_caps(self):
        self.assert_rejected(fixture(start=0, end=256), 'query_limit')
        project(fixture(start=0, end=255))
        self.assert_rejected(fixture(matches=2), 'query_limit',
                             limits=feedback.FeedbackLimits(max_matches=1))
        source = fixture()
        source['records'] = [source['records'][0]] * (feedback.MAX_INPUT_RECORDS + 1)
        self.assert_rejected(source, 'input_limit')

    def test_hard_limits_cannot_be_expanded_or_retyped(self):
        for field, ceiling in vars(feedback.FeedbackLimits()).items():
            for invalid in (ceiling + 1, 0, -1, True, 1.0, '1'):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaisesRegex(ValueError, '^invalid_feedback_limits$'):
                        feedback.FeedbackLimits(**{field: invalid})
        altered = feedback.FeedbackLimits()
        object.__setattr__(altered, 'max_records', feedback.MAX_RECORDS + 1)
        self.assert_rejected(fixture(), 'invalid_context', limits=altered)

    def test_invalid_json_duplicate_keys_depth_and_numbers_are_closed_errors(self):
        raw = encode(fixture())
        cases = [b'', b'null', b'[]', b'{', b'\xff', raw + b'{}',
                 raw.replace(b'"round_start":35', b'"round_start":35,"round_start":35'),
                 raw.replace(b'"source_replay":{', b'"source_replay":{"ignored":1,"ignored":2,'),
                 raw.replace(b'"round_start":35', b'"round_start":NaN'),
                 raw.replace(b'"round_start":35', b'"round_start":Infinity'),
                 raw.replace(b'"round_start":35', b'"round_start":1e999'),
                 raw.replace(b'"round_start":35', b'"round_start":' + b'9' * 5000)]
        for data in cases:
            with self.subTest(data=data[:25]):
                with self.assertRaises(feedback.FeedbackRejected) as caught:
                    feedback.project_replay_feedback(data, submission_id=SUBMISSION,
                        job_id=JOB, own_team_id=1, expected_spec_version='2.0.3')
                self.assertEqual(str(caught.exception), 'invalid_evidence')
        nested = b'{"ignored":' + b'[' * 33 + b'0' + b']' * 33 + b'}'
        with self.assertRaisesRegex(feedback.FeedbackRejected, '^input_limit$'):
            feedback.project_replay_feedback(nested, submission_id=SUBMISSION, job_id=JOB,
                                              own_team_id=1, expected_spec_version='2.0.3')

    def test_context_is_narrow_and_bytes_are_required(self):
        for field, invalid in [('submission_id', SECRET), ('job_id', SECRET),
                               ('submission_id', 's_' + 'a' * 32 + '\n'),
                               ('own_team_id', True), ('own_team_id', 3)]:
            kwargs = dict(submission_id=SUBMISSION, job_id=JOB, own_team_id=1,
                          expected_spec_version='2.0.3')
            kwargs[field] = invalid
            with self.assertRaisesRegex(feedback.FeedbackRejected, '^invalid_context$'):
                feedback.project_replay_feedback(encode(fixture()), **kwargs)
        for invalid in (fixture(), encode(fixture()).decode(), bytearray(encode(fixture()))):
            with self.assertRaisesRegex(feedback.FeedbackRejected, '^invalid_evidence$'):
                feedback.project_replay_feedback(invalid, submission_id=SUBMISSION,
                    job_id=JOB, own_team_id=1, expected_spec_version='2.0.3')

    def test_host_selects_expected_version_explicitly_for_each_call(self):
        for version in ('2.0.3', '3.0.13'):
            source = fixture()
            source['spec_version'] = version
            out = project(source, expected_spec_version=version)
            self.assertEqual(out['spec_version'], version)
            self.assertEqual(len(out['records']), 26)
        with self.assertRaises(TypeError):
            feedback.project_replay_feedback(encode(fixture()),
                submission_id=SUBMISSION, job_id=JOB, own_team_id=1)

    def test_cross_version_and_unsupported_evidence_are_rejected(self):
        for expected, source_version in [('2.0.3', '3.0.13'), ('3.0.13', '2.0.3'),
                                          ('3.0.13', '3.0.15'), ('2.0.3', SECRET)]:
            source = fixture()
            source['spec_version'] = source_version
            self.assert_rejected(source, expected_spec_version=expected)
        for expected in ('3.0.15', 'latest', SECRET, True, None, ['2.0.3'], {'version': '2.0.3'}):
            self.assert_rejected(fixture(), 'invalid_context', expected_spec_version=expected)

    def test_input_is_not_mutated_and_result_does_not_alias(self):
        source = fixture()
        before = deepcopy(source)
        result = project(source)
        result['records'][0]['x'] = -1
        self.assertEqual(source, before)
        self.assertEqual(project(source)['records'][0]['x'], 7)

    def test_no_raw_replay_fallback_and_facade_remains_closed(self):
        from services.scrimmage import BoundaryUnverified, ScrimmageFacade, require_heldout_attestation
        with self.assertRaisesRegex(BoundaryUnverified, 'boundary_unverified'):
            require_heldout_attestation()
        facade = ScrimmageFacade()
        submitted = facade.submit(b'not a ZIP', 'example')
        self.assertEqual(facade.replay(submitted['submission_id'])['reason'], 'replay_withheld')
        self.assertFalse(hasattr(feedback, 'download_replay'))


if __name__ == '__main__':
    unittest.main()
