"""Offline probes of selected *actual* Galaxy methods, not a Django/worker test.

GALAXY_SOURCE must name the audited checkout. Only hash-verified files are parsed.
No upstream module is imported; selected undecorated methods execute with doubles.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import posixpath
from types import SimpleNamespace as NS
import unittest

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "source-manifest.json").read_text())


def source_root():
    value = os.environ.get("GALAXY_SOURCE")
    if not value:
        raise RuntimeError("Set GALAXY_SOURCE to the pinned Galaxy source checkout")
    return Path(value)


def source(path):
    data = (source_root() / path).read_bytes()
    expected = MANIFEST["files"][path]
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError(f"Historical source hash mismatch: {path}")
    return ast.parse(data, filename=path)


def method(path, cls, name, globals_):
    tree = source(path)
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    node = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == name)
    if node.decorator_list:
        raise RuntimeError("Decorated methods require real integration tests")
    namespace = dict(globals_)
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace[name]


MODELS = "backend/siarnaq/api/compete/models.py"
SERIALIZERS = "backend/siarnaq/api/compete/serializers.py"
EPISODES = "backend/siarnaq/api/episodes/models.py"


def choices(cls):
    owner = next(n for n in source(MODELS).body if isinstance(n, ast.ClassDef) and n.name == cls)
    return NS(**{n.targets[0].id: ast.literal_eval(n.value) for n in owner.body
                 if isinstance(n, ast.Assign)})


class Log:
    def bind(self, **kw):
        return self

    def info(self, *args, **kw):
        pass

    warn = info


class Finalized(Exception):
    pass


class Relation:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class Contracts(unittest.TestCase):
    def setUp(self):
        self.settings = NS(GCLOUD_BUCKET_SECURE="fixture-secure", ALLOWED_HOSTS=["replica.invalid"], SATURN_MAX_FAILURES=3)
        # This reverse double is NOT proof Django routing works. Routes are separately
        # recorded and must be tested against real DRF in the integration gate.
        def reverse(name, kwargs):
            kind = name.removesuffix("-report")
            return f'/api/compete/{kwargs["episode_id"]}/{kind}/{kwargs["pk"]}/report/'
        self.env = dict(settings=self.settings, posixpath=posixpath, reverse=reverse)
        self.episode = NS(pk=2023, name_short="bc23", language="java8", scaffold="file:///fixture/scaffold")
        self.bind(self.episode, EPISODES, "Episode", ["for_saturn"])

    def bind(self, obj, path, cls, names):
        for name in names:
            setattr(obj, name, method(path, cls, name, self.env).__get__(obj))
        return obj

    def submission(self, pk, team):
        return self.bind(NS(pk=pk, episode=self.episode, team=NS(name=team), package="fixture"),
                         MODELS, "Submission", ["get_source_path", "get_binary_path", "for_saturn", "enqueue_options"])

    def test_compile_payload_golden(self):
        expected = json.loads((HERE / "fixtures/compile.json").read_text())
        self.assertEqual(self.submission(11, "Synthetic A").enqueue_options(), expected)

    def test_execute_payload_golden(self):
        match = self.bind(NS(pk=31, episode=self.episode, replay="00000000-0000-0000-0000-000000000031",
                            alternate_order=True, maps=Relation([NS(name="FixtureMapOne"), NS(name="FixtureMapTwo")]),
                            participants=Relation([NS(player_index=1, submission=self.submission(12, "Synthetic B")),
                                                   NS(player_index=0, submission=self.submission(11, "Synthetic A"))])),
                          MODELS, "Match", ["get_replay_path", "for_saturn", "enqueue_options"])
        expected = json.loads((HERE / "fixtures/execute.json").read_text())
        self.assertEqual(match.enqueue_options(), expected)

    def invocation(self, status="QUE", failures=0):
        obj = NS(pk=1, status=status, logs="prior\n", num_failures=failures, saved=[])
        obj.save = lambda **kw: obj.saved.append(kw)
        self.env["SaturnStatus"] = choices("SaturnStatus")
        return self.bind(obj, MODELS, "SaturnInvocation", ["is_finalized"])

    def report(self, obj, **data):
        env = {**self.env, "SaturnStatus": choices("SaturnStatus"), "logger": Log(), "AlreadyFinalized": Finalized}
        return method(SERIALIZERS, "SaturnInvocationSerializer", "update", env)(None, obj, data)

    def test_failed_compile_is_completed_but_unaccepted(self):
        # Wire fixture distinguishes task success from bot acceptance. Actual
        # SubmissionReportSerializer transaction/accepted mutation remains untested.
        report = json.loads((HERE / "fixtures/compile-rejected-report.json").read_text())
        obj = self.invocation()
        self.report(obj, **report["invocation"])
        self.assertTrue(obj.is_finalized())
        self.assertFalse(report["accepted"])
        self.assertEqual(obj.num_failures, 0)

    def test_interrupted_retry_does_not_consume_failure_budget(self):
        obj = self.invocation()
        self.report(obj, status="TRY", interrupted=True, logs="interrupted\n")
        self.assertEqual((obj.status, obj.num_failures, obj.logs), ("TRY", 0, "prior\ninterrupted\n"))

    def test_retry_consumes_budget_and_exhausts(self):
        obj = self.invocation(failures=2)
        self.report(obj, status="TRY", interrupted=False)
        self.assertEqual((obj.status, obj.num_failures), ("ERR", 3))
        self.assertTrue(obj.is_finalized())
        self.assertIn("Maximum retries reached", obj.logs)

    def test_terminal_repeated_reports_are_rejected_without_mutation(self):
        for status in ["OK!", "ERR", "CAN"]:
            with self.subTest(status=status):
                obj = self.invocation(status)
                with self.assertRaises(Finalized):
                    self.report(obj, status="RUN", logs="duplicate")
                self.assertEqual((obj.status, obj.logs, obj.saved), (status, "prior\n", []))

    def test_nonterminal_statuses(self):
        for status in ["NEW", "QUE", "RUN", "TRY"]:
            with self.subTest(status=status):
                self.assertFalse(self.invocation(status).is_finalized())

    def test_public_domain_codes(self):
        self.assertEqual(vars(choices("ScrimmageRequestStatus")), {"PENDING": "P", "ACCEPTED": "Y", "REJECTED": "N"})
        self.assertEqual(vars(choices("PlayerOrder")), {"REQUESTER_FIRST": "+", "REQUESTER_LAST": "-", "SHUFFLED": "?"})

    def test_request_order_and_alternation(self):
        self.env["PlayerOrder"] = choices("PlayerOrder")
        for order, bit, expected, alternating in [
            ("+", 0, [11, 12], False), ("-", 1, [12, 11], False),
            ("?", 0, [11, 12], True), ("?", 1, [12, 11], True),
        ]:
            with self.subTest(order=order, bit=bit):
                self.env["random"] = NS(getrandbits=lambda n: bit)
                obj = self.bind(NS(player_order=order, requested_by_id=11, requested_to_id=12),
                                MODELS, "ScrimmageRequest", ["determine_order", "determine_is_alternating"])
                self.assertEqual(obj.determine_order(), expected)
                self.assertEqual(obj.determine_is_alternating(), alternating)

    def test_null_publisher_receipt_is_not_work(self):
        from concurrent.futures import Future
        path = "backend/siarnaq/gcloud/saturn.py"
        namespace = {"Future": Future}
        node = next(n for n in source(path).body if isinstance(n, ast.ClassDef) and n.name == "NullPublisher")
        exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
        publisher = namespace["NullPublisher"]()
        self.assertEqual(publisher.publish(data=b"not even a payload").result(), "none")
        self.assertEqual(publisher.topic_path("synthetic-project", "compile"), "nowhere")

    def test_all_audited_source_hashes(self):
        for path, expected in MANIFEST["files"].items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((source_root() / path).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
