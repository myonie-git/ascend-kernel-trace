"""Offline parser/control-flow tests. Fake capture is not hardware validation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import collect
import inspect_trace
import probe_env

FIXTURES = ROOT / "tests" / "fixtures"


class TraceTests(unittest.TestCase):
    def test_instruction_evidence_and_units(self):
        report = inspect_trace.inspect_trace(FIXTURES / "instruction.json")
        self.assertEqual(report["granularity"], "instruction")
        self.assertEqual(report["declared_source"], "unknown")
        self.assertEqual(report["instruction_event_count"], 5)
        self.assertAlmostEqual(report["event_span_us"], 1.2)
        vector = next(lane for lane in report["lanes"] if lane["process"] == "VECTOR")
        self.assertAlmostEqual(vector["interval_union_us"], 0.7)

    def test_operator_named_like_opcode_is_not_instruction(self):
        report = inspect_trace.inspect_trace(FIXTURES / "operator.json", "hardware-instruction")
        self.assertEqual(report["granularity"], "operator")
        self.assertEqual(report["instruction_event_count"], 0)
        self.assertTrue(any("not confirmed" in warning for warning in report["warnings"]))

    def test_unknown_events_and_bad_intervals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            path.write_text(json.dumps([{"ph": "X", "name": "VCONV", "ts": 0, "dur": 1},
                                        {"ph": "X", "name": "bad", "ts": 1, "dur": -1},
                                        {"ph": "B", "name": "begin", "ts": 2}]))
            report = inspect_trace.inspect_trace(path)
            self.assertEqual(report["granularity"], "undetermined")
            self.assertEqual(report["invalid_event_count"], 1)
            self.assertEqual(report["unsupported_begin_end_event_count"], 1)

    def test_size_limit(self):
        with self.assertRaisesRegex(ValueError, "max-bytes"):
            inspect_trace.inspect_trace(FIXTURES / "instruction.json", max_bytes=1)

    def test_discovery_avoids_combined_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = root / "simulator" / "core0.veccore0"
            core.mkdir(parents=True)
            for file in (core / "trace.json", core.parent / "trace.json"):
                shutil.copyfile(FIXTURES / "instruction.json", file)
            self.assertEqual(inspect_trace.discover_traces(root), [core / "trace.json"])

    def test_malformed_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            path.write_text('{"traceEvents": [')
            with self.assertRaises(ValueError):
                inspect_trace.inspect_trace(path)

    def test_numeric_string_timestamps_preserve_small_intervals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            path.write_text(json.dumps([
                {"ph": "X", "name": "sample", "pid": 1, "tid": 0,
                 "ts": "1793890268823223.175", "dur": "0.010",
                 "args": {"Task Type": "AI_VECTOR_CORE"}},
                {"ph": "X", "name": "sample", "pid": 1, "tid": 0,
                 "ts": "1793890268823223.185", "dur": "0.010",
                 "args": {"Task Type": "AI_VECTOR_CORE"}},
            ]))
            report = inspect_trace.inspect_trace(path)
            self.assertEqual(report["granularity"], "operator")
            self.assertEqual(report["operator_event_count"], 2)
            self.assertAlmostEqual(report["event_span_us"], 0.020)
            self.assertAlmostEqual(report["lanes"][0]["interval_union_us"], 0.020)

    def test_pipeline_without_valid_pc_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            document = json.loads((FIXTURES / "instruction.json").read_text())
            for event in document["traceEvents"]:
                if event.get("ph") == "X":
                    event["args"]["pc_addr"] = "unknown"
            path.write_text(json.dumps(document))
            self.assertEqual(inspect_trace.inspect_trace(path)["granularity"], "undetermined")


class ProbeTests(unittest.TestCase):
    def test_no_cann_or_tools_is_a_report_not_a_device_probe(self):
        with mock.patch.dict(os.environ, {"PATH": "", "ASCEND_HOME_PATH": "",
                                          "ASCEND_TOOLKIT_HOME": ""}):
            report = probe_env.probe_environment()
        self.assertIsNone(report["selected_simulator_prefix"])
        self.assertEqual(report["device_access"], "no explicit device query")

    def test_probe_timeout_retains_partial_output(self):
        result = probe_env.query([sys.executable, "-u", "-c",
                                  'import time; print("partial version"); time.sleep(30)'], timeout=0.2)
        self.assertIsNone(result["returncode"])
        self.assertIn("partial version", result["text"])
        self.assertIn("timed out", result["error"])

    def test_explicit_root_version_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "version.info").write_text("version=example\nsecret=not-reported\n")
            (root / "tools/simulator/ExampleSoC/lib").mkdir(parents=True)
            report = probe_env.cann_info(directory, "ExampleSoC")
            self.assertEqual(len(report["simulator_directories"]), 1)
            self.assertEqual(report["version_fields"][0]["value"], "version=example")


@unittest.skipUnless(sys.platform.startswith("linux"), "Collector subprocess tests require Linux")
class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trace skill test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = self.root / "msopprof"
        shutil.copyfile(FIXTURES / "fake_profiler.py", self.tool)
        self.tool.chmod(0o700)
        for name in ("instruction.json", "operator.json"):
            shutil.copyfile(FIXTURES / name, self.root / name)
        self.output = self.root / "result"
        self.marker = self.root / "ran"
        self.env = dict(os.environ, FAKE_RUN_MARKER=str(self.marker), PYTHONDONTWRITEBYTECODE="1")

    def run_collect(self, mode="complete", execute=True, extra=(), app_args=()):
        argv = [sys.executable, str(ROOT / "scripts/collect.py"), "--soc", "ExampleSoC",
                "--kernel-name", "sample_kernel", "--output", str(self.output),
                "--tool", str(self.tool), "--workdir", str(self.root)]
        if execute:
            argv.append("--execute")
        argv += list(extra) + ["--", sys.executable, "-c", "pass"] + list(app_args)
        return subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=dict(self.env, FAKE_PROFILER_MODE=mode), timeout=20)

    def test_default_is_dry_run_and_no_output_is_created(self):
        result = self.run_collect(execute=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "planned")
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.output.exists())

    def test_capture_manifest_hash_and_literal_arguments(self):
        literal = "$(touch should-not-exist); an argument with spaces"
        result = self.run_collect(app_args=[literal])
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["source"], "simulator")
        self.assertTrue(manifest["target_match_confirmed"])
        self.assertEqual(manifest["command_argv"][-1], literal)
        self.assertEqual(len(manifest["traces"][0]["sha256"]), 64)
        self.assertFalse((self.root / "should-not-exist").exists())

    def test_existing_output_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_text("unchanged")
        result = self.run_collect()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), "unchanged")
        self.assertFalse(self.marker.exists())

    def test_symlink_output_is_rejected(self):
        self.output.symlink_to(self.root / "destination", target_is_directory=True)
        result = self.run_collect()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_unsupported_cli_is_not_executed(self):
        result = self.run_collect(mode="unsupported")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.output.exists())

    def test_msprof_op_frontend(self):
        renamed = self.root / "msprof"
        self.tool.rename(renamed)
        self.tool = renamed
        result = self.run_collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["command_argv"][1:3], ["op", "simulator"])

    def test_tool_timeout_with_valid_trace_is_partial(self):
        result = self.run_collect(mode="tool-timeout")
        self.assertEqual(result.returncode, 2)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["traces"][0]["granularity"], "instruction")

    def test_wall_timeout_retains_manifest(self):
        result = self.run_collect(mode="hang", extra=["--wall-timeout-seconds", "0.1"])
        self.assertEqual(result.returncode, 2, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertTrue(manifest["interrupted"])
        self.assertEqual(manifest["status"], "partial")

    def test_failure_and_insufficient_evidence_never_pass(self):
        for mode, status in (("failed", "failed"), ("operator", "unverified"),
                             ("empty", "unverified"), ("malformed", "unverified"),
                             ("wrong-kernel", "unverified"), ("missing-completion", "unverified")):
            with self.subTest(mode=mode):
                self.output = self.root / mode
                result = self.run_collect(mode=mode)
                self.assertNotEqual(result.returncode, 0)
                manifest = json.loads((self.output / "manifest.json").read_text())
                self.assertEqual(manifest["status"], status)


if __name__ == "__main__":
    unittest.main()
