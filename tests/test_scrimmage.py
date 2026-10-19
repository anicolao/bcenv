"""Synthetic hostile intake/public-boundary probes; no bots or engine execution."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
import stat
import struct
import sys
import unittest
import warnings
from unittest.mock import patch
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.scrimmage import (AcceptedSubmission, BoundaryAttestation,
    BoundaryUnverified, IntakeLimits, QuotaExceeded, Quotas, ScrimmageFacade,
    SubmissionRejected, accept_source_zip, public_result, require_heldout_attestation)

SOURCE = b'package bot; public class RobotPlayer {}\n'
SUBMISSION_ID = 's_' + 'a' * 32


def archive(entries=None, compression=zipfile.ZIP_STORED, comment=b''):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=compression) as output:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            for name, data in entries or [('bot/RobotPlayer.java', SOURCE)]:
                output.writestr(name, data)
        output.comment = comment
    return stream.getvalue()


def patch_zip(data, *, local=None, central=None, end=None):
    """Patch synthetic ZIP integer metadata at format-defined offsets."""
    output = bytearray(data)
    central_start = output.index(b'PK\x01\x02')
    for start, changes in ((0, local), (central_start, central), (len(data) - 22, end)):
        for offset, fmt, value in changes or []:
            struct.pack_into(fmt, output, start + offset, value)
    return bytes(output)


class IntakeTests(unittest.TestCase):
    def assert_rejected(self, data, package='bot', code=None, limits=None):
        with self.assertRaises(SubmissionRejected) as caught:
            accept_source_zip(data, package, limits)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn('bot/', str(caught.exception))

    def test_valid_stored_and_deflated(self):
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            result = accept_source_zip(archive(compression=method), 'bot')
            self.assertIsInstance(result, AcceptedSubmission)
            self.assertEqual(result.files[0].data, SOURCE)
            self.assertEqual(result.files[0].path, 'bot/RobotPlayer.java')
            self.assertRegex(result.content_sha256, r'^[a-f0-9]{64}$')

    def test_generated_source_sized_inputs_fit_bounded_defaults(self):
        # Public historical generated Java exceeds the old128KiB per-file cap.
        source = b'/*' + b'x' * 269_814 + b'*/'
        result = accept_source_zip(archive([('bot/Large.java', source)]), 'bot')
        self.assertEqual(len(result.files[0].data), 269_818)
        limits = IntakeLimits()
        self.assertEqual(limits.max_file_bytes, 524_288)
        self.assertEqual(limits.max_source_bytes, 2_097_152)
        self.assert_rejected(archive([('bot/Large.java', b'x' * (limits.max_file_bytes + 1))]), code='archive_limit')

    def test_immutable_normalized_content(self):
        result = accept_source_zip(archive(), 'bot')
        with self.assertRaises(FrozenInstanceError):
            result.package = 'other'
        with self.assertRaises(FrozenInstanceError):
            result.files[0].data = b'changed'
        with self.assertRaises(TypeError):
            result.files[0] = result.files[0]

    def test_content_identity_ignores_archive_order_compression_and_timestamp(self):
        entries = [('bot/RobotPlayer.java', SOURCE), ('bot/helper/Util.java', b'package bot.helper; class Util {}')]
        first = accept_source_zip(archive(entries), 'bot')
        info = zipfile.ZipInfo('bot/RobotPlayer.java', date_time=(2000, 1, 1, 0, 0, 0))
        second_entries = [entries[1], (info, SOURCE)]
        second = accept_source_zip(archive(second_entries, zipfile.ZIP_DEFLATED), 'bot')
        self.assertEqual(first, second)
        changed = accept_source_zip(archive([(entries[0][0], SOURCE + b' '), entries[1]]), 'bot')
        self.assertNotEqual(first.content_sha256, changed.content_sha256)

    def test_valid_nested_package(self):
        result = accept_source_zip(archive([('legal/bot/RobotPlayer.java', SOURCE)]), 'legal.bot')
        self.assertEqual(result.package, 'legal.bot')

    def test_invalid_packages(self):
        for package in ('', '../bot', '/bot', 'bot/', 'bot\\sub', 'bot..sub',
                        'class', 'bot.class', 'bot-name', '9bot', 'böt', 'nul', '_', None, [], True):
            with self.subTest(package=package):
                self.assert_rejected(archive(), package, 'invalid_package')

    def test_path_surprises(self):
        for name in ('../bot/RobotPlayer.java', '/bot/RobotPlayer.java',
                     'C:/bot/RobotPlayer.java', 'bot\\RobotPlayer.java',
                     'bot//RobotPlayer.java', 'bot/./RobotPlayer.java',
                     'bot/../RobotPlayer.java', 'bot/sub/../../RobotPlayer.java',
                     'other/RobotPlayer.java', 'bot/.hidden.java',
                     'bot/RobotPlayer.java/', 'bot/RobotPlayer.JAVA',
                     'bot/RobotPlayer.java ', 'bot/RobotPlayer.java:stream',
                     'bot/CON.java', 'bot/NUL/RobotPlayer.java',
                     'bot/7Robot.java', 'bot/class.java', 'bot/Robot\nPlayer.java',
                     'bot/RobötPlayer.java', 'bot/sub.class', 'bot/lib.jar',
                     'bot/.DS_Store', 'bot/RobotPlayer.class', 'bot/'):
            with self.subTest(name=name):
                self.assert_rejected(archive([(name, SOURCE)]), code='invalid_archive')

    def test_embedded_nul_name(self):
        data = archive([('bot/RobotPlayerXjava', SOURCE)])
        # ZipInfo writers truncate NUL names; patch both headers afterward.
        data = data.replace(b'RobotPlayerXjava', b'RobotPlayer\x00java')
        self.assert_rejected(data, code='invalid_archive')

    def test_symlink_fifo_socket_and_directory_attributes(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFDIR):
            info = zipfile.ZipInfo('bot/RobotPlayer.java')
            info.create_system = 3
            info.external_attr = (kind | 0o644) << 16
            with self.subTest(kind=kind):
                self.assert_rejected(archive([(info, SOURCE)]))
        info = zipfile.ZipInfo('bot/RobotPlayer.java')
        info.external_attr = 0x10
        self.assert_rejected(archive([(info, SOURCE)]))

    def test_duplicate_and_case_ambiguous_files_or_directories(self):
        for first, second in (('bot/A.java', 'bot/A.java'), ('bot/A.java', 'bot/a.java'),
                              ('bot/Helper/A.java', 'bot/helper/B.java')):
            with self.subTest(first=first, second=second):
                self.assert_rejected(archive([(first, SOURCE), (second, SOURCE)]))

    def test_archive_count_expansion_size_and_path_limits(self):
        cases = [
            (archive(), IntakeLimits(max_archive_bytes=20)),
            (archive([('bot/A.java', SOURCE), ('bot/B.java', SOURCE)]), IntakeLimits(max_files=1)),
            (archive(), IntakeLimits(max_file_bytes=1)),
            (archive([('bot/A.java', SOURCE), ('bot/B.java', SOURCE)]), IntakeLimits(max_source_bytes=len(SOURCE))),
            (archive([('bot/A.java', b'a' * 10000)], zipfile.ZIP_DEFLATED), IntakeLimits(max_expansion_ratio=2)),
        ]
        for data, limits in cases:
            with self.subTest(limits=limits):
                self.assert_rejected(data, code='archive_limit', limits=limits)
        self.assert_rejected(archive(), limits=IntakeLimits(max_path_bytes=10))
        self.assert_rejected(archive([('bot/a/A.java', SOURCE)]), limits=IntakeLimits(max_package_depth=1))

    def test_empty_archive_and_non_bytes(self):
        stream = io.BytesIO()
        zipfile.ZipFile(stream, 'w').close()
        self.assert_rejected(stream.getvalue(), code='archive_limit')
        for data in (b'', b'not a zip', '', bytearray(archive()), None):
            self.assert_rejected(data)

    def test_empty_non_utf8_or_nul_source(self):
        for content in (b'', b'\xff\xfe\x00', b'package bot;\x00'):
            self.assert_rejected(archive([('bot/A.java', content)]), code='invalid_source')

    def test_metadata_channels_comments_extra_fields_and_unsupported_compression(self):
        self.assert_rejected(archive(comment=b'internal/opponent/path'))
        for field in ('extra', 'comment'):
            info = zipfile.ZipInfo('bot/RobotPlayer.java')
            setattr(info, field, b'\x01\x00\x00\x00')
            self.assert_rejected(archive([(info, SOURCE)]))
        data = patch_zip(archive(), local=[(8, '<H', 99)], central=[(10, '<H', 99)])
        self.assert_rejected(data)

    def test_prefix_trailer_and_truncation(self):
        data = archive()
        for bad in (b'#!/bin/sh\n' + data, data + b'payload', data[:-1], data[:35]):
            self.assert_rejected(bad)

    def test_crc_and_local_central_name_mismatch(self):
        data = archive()
        self.assert_rejected(patch_zip(data, local=[(14, '<L', 1)], central=[(16, '<L', 1)]))
        first = data.find(b'RobotPlayer')
        self.assert_rejected(data[:first] + b'robotPlayer' + data[first + len(b'RobotPlayer'):])

    def test_multipart_zip64_flags_and_declared_size_lies(self):
        cases = [
            patch_zip(archive(), end=[(4, '<H', 1)]),
            patch_zip(archive(), local=[(4, '<H', 45)], central=[(6, '<H', 45)]),
            patch_zip(archive(), local=[(6, '<H', 1)], central=[(8, '<H', 1)]),
            patch_zip(archive(), local=[(6, '<H', 8)], central=[(8, '<H', 8)]),
            patch_zip(archive(), central=[(42, '<L', 1)]),
            patch_zip(archive(), local=[(22, '<L', 1)], central=[(24, '<L', 1)]),
            patch_zip(archive(), local=[(18, '<L', 999999)], central=[(20, '<L', 999999)]),
        ]
        for data in cases:
            self.assert_rejected(data)

    def test_underdeclared_deflate_output_cannot_be_silently_truncated(self):
        content = b'package bot;' + b' ' * 5000
        data = archive([('bot/A.java', content)], zipfile.ZIP_DEFLATED)
        # A malicious declared size/CRC matching only the first output byte.
        data = patch_zip(data, local=[(22, '<L', 1), (14, '<L', zlib.crc32(content[:1]))],
                         central=[(24, '<L', 1), (16, '<L', zlib.crc32(content[:1]))])
        self.assert_rejected(data)

    def test_forged_small_entry_count_rejected_before_zipfile_allocates_records(self):
        data = archive([('bot/A%d.java' % n, SOURCE) for n in range(2000)])
        data = patch_zip(data, end=[(8, '<H', 1), (10, '<H', 1)])
        with patch('services.scrimmage.zipfile.ZipFile', side_effect=AssertionError('must not parse')):
            self.assert_rejected(data, code='invalid_archive')

    def test_limits_reject_bool_zero_and_negative(self):
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                IntakeLimits(max_files=value)


class PublicBoundaryTests(unittest.TestCase):
    def complete(self):
        return {'status': 'complete', 'validated_complete': True,
                'summary': {'games': 3, 'wins': 1, 'draws': 1, 'losses': 1}}

    def test_allowlist_strips_all_internal_fields_at_every_level(self):
        evidence = self.complete()
        private = 'SECRET_HELDOUT_PATH_AND_IDENTITY'
        evidence.update({'submission_id': private, 'opponent': private,
                         'opponent_sha256': private, 'schedule': [private],
                         'log': private, 'error': private, 'replay_url': private,
                         'source': private, 'command': private, 'map': private})
        evidence['summary'].update({'opponent': private, 'log': private, 'episodes': [private]})
        result = public_result(SUBMISSION_ID, evidence)
        self.assertNotIn(private, json.dumps(result))
        self.assertEqual(set(result), {'schema', 'submission_id', 'status', 'summary'})
        self.assertEqual(set(result['summary']), {'games', 'wins', 'draws', 'losses'})
        self.assertEqual(result['submission_id'], SUBMISSION_ID)

    def test_bad_complete_evidence_never_becomes_loss_or_win(self):
        cases = [None, [], {}, {'status': 'complete'}, {'status': 'succeeded', 'exit_code': 0},
                 {'status': 'complete', 'validated_complete': 1, 'summary': self.complete()['summary']}]
        for patch in ({'wins': -1}, {'wins': True}, {'wins': 1.0}, {'wins': '1'},
                      {'wins': 9000}, {'games': 4}, {'games': 0, 'wins': 0, 'draws': 0, 'losses': 0}):
            evidence = self.complete()
            evidence['summary'].update(patch)
            cases.append(evidence)
        for evidence in cases:
            with self.subTest(evidence=evidence):
                result = public_result(SUBMISSION_ID, evidence)
                self.assertEqual(result['status'], 'infrastructure_failure')
                self.assertNotIn('summary', result)

    def test_rejection_infrastructure_failure_and_accepted_distinct(self):
        for state, reason in (('rejected', 'compile_rejected'),
                              ('infrastructure_failure', 'worker_unavailable')):
            result = public_result(SUBMISSION_ID, {'status': state, 'reason': reason})
            self.assertEqual((result['status'], result['reason']), (state, reason))
            self.assertNotIn('summary', result)
        self.assertEqual(public_result(SUBMISSION_ID, {'status': 'accepted'})['status'], 'accepted')

    def test_unknown_free_text_reasons_are_not_copied(self):
        for status in ('rejected', 'infrastructure_failure'):
            for reason in ('/private/heldout/bot.java', ['internal'], {'internal': True}):
                result = public_result(SUBMISSION_ID, {'status': status, 'reason': reason})
                self.assertNotIn('heldout', json.dumps(result))
                self.assertIsInstance(result['reason'], str)

    def test_response_bound_and_unknown_ids(self):
        with self.assertRaisesRegex(ValueError, 'public_response_limit'):
            public_result(SUBMISSION_ID, self.complete(), max_bytes=1)
        for identifier in ('/private/path', 's_../secret', None, [], 's_' + 'Z' * 32):
            with self.assertRaises(LookupError):
                public_result(identifier, self.complete())


class FacadeTests(unittest.TestCase):
    def setUp(self):
        self.time = 100.0
        self.facade = ScrimmageFacade(clock=lambda: self.time)

    def complete(self):
        return {'status': 'complete', 'validated_complete': True,
                'summary': {'games': 1, 'wins': 1, 'draws': 0, 'losses': 0}}

    def submit(self, facade=None):
        return (facade or self.facade).submit(archive(), 'bot')['submission_id']

    def test_submit_read_finish_and_no_scheduler_side_effects(self):
        identifier = self.submit()
        self.assertRegex(identifier, r'^s_[0-9a-f]{32}$')
        self.assertEqual(self.facade.result(identifier)['status'], 'accepted')
        self.assertEqual(self.facade.accepted_submission(identifier).files[0].data, SOURCE)
        result = self.facade.record_result(identifier, self.complete())
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(self.facade.result(identifier), result)
        self.assertNotIn('content_sha256', result)
        with self.assertRaisesRegex(ValueError, 'terminal_result'):
            self.facade.record_result(identifier, self.complete())

    def test_rejected_intake_is_terminal_and_not_a_game_loss(self):
        receipt = self.facade.submit(b'bad', 'bot')
        self.assertEqual(receipt['status'], 'rejected')
        self.assertIsNone(self.facade.accepted_submission(receipt['submission_id']))
        self.assertNotIn('summary', receipt)
        with self.assertRaises(ValueError):
            self.facade.record_result(receipt['submission_id'], self.complete())

    def test_result_and_receipt_mutation_does_not_change_stored_state(self):
        receipt = self.facade.submit(archive(), 'bot')
        identifier = receipt['submission_id']
        receipt['status'] = 'complete'
        self.assertEqual(self.facade.result(identifier)['status'], 'accepted')
        result = self.facade.record_result(identifier, self.complete())
        result['summary']['wins'] = 999
        result = self.facade.result(identifier)
        result['summary']['wins'] = 999
        self.assertEqual(self.facade.result(identifier)['summary']['wins'], 1)

    def test_replay_never_exposes_raw_bytes_or_paths(self):
        identifier = self.submit()
        self.facade.record_result(identifier, self.complete())
        result = self.facade.replay(identifier)
        self.assertEqual(result, {'schema': 'bcenv.scrimmage.v1', 'submission_id': identifier,
                                 'status': 'unavailable', 'reason': 'replay_withheld'})
        with self.assertRaises(LookupError):
            self.facade.replay(SUBMISSION_ID)

    def test_rate_counts_rejections_and_recovers_after_window(self):
        facade = ScrimmageFacade(quotas=Quotas(submissions_per_window=2, submission_window_seconds=10), clock=lambda: self.time)
        facade.submit(b'bad', 'bot')
        facade.submit(b'bad', 'bot')
        with self.assertRaisesRegex(QuotaExceeded, 'rate_limit'):
            facade.submit(archive(), 'bot')
        self.time += 10
        self.assertEqual(facade.submit(archive(), 'bot')['status'], 'accepted')

    def test_shared_read_limit_counts_missing_ids_and_replay(self):
        facade = ScrimmageFacade(quotas=Quotas(reads_per_window=2, read_window_seconds=10), clock=lambda: self.time)
        identifier = self.submit(facade)
        with self.assertRaises(LookupError):
            facade.result(SUBMISSION_ID)
        facade.replay(identifier)
        with self.assertRaisesRegex(QuotaExceeded, 'rate_limit'):
            facade.result(identifier)
        self.time += 10
        self.assertEqual(facade.result(identifier)['status'], 'accepted')

    def test_pending_and_total_record_quotas(self):
        facade = ScrimmageFacade(quotas=Quotas(max_pending=1, max_records=2))
        identifier = self.submit(facade)
        with self.assertRaisesRegex(QuotaExceeded, 'pending_limit'):
            self.submit(facade)
        facade.record_result(identifier, self.complete())
        identifier = self.submit(facade)
        facade.record_result(identifier, self.complete())
        with self.assertRaisesRegex(QuotaExceeded, 'record_limit'):
            self.submit(facade)

    def test_results_are_owner_scoped_and_ids_not_content_hashes(self):
        first = self.submit()
        other = ScrimmageFacade()
        second = self.submit(other)
        self.assertNotEqual(first, second)
        self.assertEqual(self.facade.accepted_submission(first), other.accepted_submission(second))
        with self.assertRaises(LookupError):
            other.result(first)

    def test_bad_reports_fail_closed_and_nonterminal_reports_are_rejected(self):
        identifier = self.submit()
        with self.assertRaisesRegex(ValueError, 'terminal_report_required'):
            self.facade.record_result(identifier, {'status': 'accepted'})
        result = self.facade.record_result(identifier, {'status': 'succeeded', 'exit_code': 0})
        self.assertEqual(result['status'], 'infrastructure_failure')

    def test_concurrent_pending_and_rate_limits_are_atomic(self):
        for quotas, expected in ((Quotas(max_pending=4, submissions_per_window=64), 4),
                                 (Quotas(max_pending=32, submissions_per_window=7), 7)):
            facade = ScrimmageFacade(quotas=quotas)
            data = archive()

            def submit(unused):
                try:
                    return facade.submit(data, 'bot')['status']
                except QuotaExceeded:
                    return 'denied'

            with ThreadPoolExecutor(max_workers=16) as pool:
                outcomes = list(pool.map(submit, range(32)))
            self.assertEqual(outcomes.count('accepted'), expected)
            self.assertEqual(outcomes.count('denied'), 32 - expected)

    def test_quota_configuration_validation(self):
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                Quotas(max_pending=value)
        with self.assertRaises(ValueError):
            Quotas(max_public_bytes=511)


class PackagingTests(unittest.TestCase):
    def test_trusted_scaffold_is_outside_competitor_package_inputs(self):
        # Guard the current explicit package copy/filter, not a sandbox proof.
        package = (ROOT / 'nix/bcenv.nix').read_text()
        self.assertIn('cp -r runtime seasons skills prompts contracts "$out/lib/bcenv/"', package)
        self.assertNotIn('lib.hasPrefix "services/"', package)
        self.assertFalse((ROOT / 'runtime/scrimmage.py').exists())
        self.assertTrue((ROOT / 'services/scrimmage.py').is_file())
        for materializer in ('materialize-local-pilot.py', 'materialize-lecture-trial.py'):
            self.assertNotIn("/ 'services'", (ROOT / 'scripts' / materializer).read_text())


class HeldoutGateTests(unittest.TestCase):
    def test_missing_forged_expired_or_all_true_attestations_cannot_open_gate(self):
        attestation = BoundaryAttestation('synthetic-deployment', 1, 99999999999,
                                          True, True, True, 'a' * 64)
        for value in (None, True, {'allow_heldout': True}, attestation,
                      replace(attestation, expires_at=1),
                      replace(attestation, filesystem_boundary_validated=False)):
            with self.subTest(value=value):
                with self.assertRaisesRegex(BoundaryUnverified, 'boundary_unverified'):
                    require_heldout_attestation(value, deployment_id='synthetic-deployment')

    def test_no_verifier_or_enabled_flag_is_supported(self):
        for kwargs in ({'enabled': True}, {'allow_heldout': True},
                       {'independent_verifier': lambda unused: True}):
            with self.assertRaises(TypeError):
                require_heldout_attestation(**kwargs)


if __name__ == '__main__':
    unittest.main()
