"""Contract tests for script metadata, independent of evaluation environments."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from validate_baseline_meta import load_json, validate_metadata


VALIDATOR = Path(__file__).with_name("validate_baseline_meta.py")
EXAMPLE = Path(__file__).with_name("baseline_meta.example.json")


class BaselineMetadataTests(unittest.TestCase):
    def setUp(self):
        self.meta = load_json(EXAMPLE)

    def assert_rejected(self, fragment, meta):
        errors = validate_metadata(meta)
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_example_and_separate_scripts(self):
        self.assertEqual(validate_metadata(self.meta), [])
        for mode in ("verify", "benchmark"):
            self.meta["evaluation"][mode] = {
                "script": f"eval/{mode}.py",
                "command": ["python3", f"eval/{mode}.py"],
            }
            self.meta["protected_files"].append({
                "path": f"eval/{mode}.py", "checksum": "0" * 64,
            })
        self.assertEqual(validate_metadata(self.meta), [])

    def test_shared_script_requires_distinct_mode_commands(self):
        self.meta["evaluation"]["benchmark"] = copy.deepcopy(
            self.meta["evaluation"]["verify"]
        )
        self.assert_rejected("shared script requires distinct", self.meta)

    def test_interface_structure(self):
        mutations = [
            (lambda m: m.pop("evaluation"), "required property"),
            (lambda m: m.update(schema_version=2), "/schema_version"),
            (lambda m: m["evaluation"].pop("verify"), "/evaluation"),
            (lambda m: m["evaluation"]["verify"].update(command="python3 verify.py"), "/evaluation/verify/command"),
            (lambda m: m["evaluation"]["verify"].update(command=[]), "/evaluation/verify/command"),
            (lambda m: m["evaluation"]["verify"].update(command=[" "]), "/evaluation/verify/command"),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                meta = copy.deepcopy(self.meta)
                mutate(meta)
                self.assert_rejected(expected, meta)

    def test_script_paths_are_project_relative(self):
        for path in ("/eval/verify.py", "../verify.py", ".", ""):
            with self.subTest(path=path):
                meta = copy.deepcopy(self.meta)
                meta["evaluation"]["verify"]["script"] = path
                self.assert_rejected("/evaluation/verify/script", meta)

    def test_runtime_and_management_fields_are_not_metadata(self):
        for field in (
            "definition", "reproduction", "acceptance", "artifacts",
            "target",
        ):
            with self.subTest(field=field):
                meta = copy.deepcopy(self.meta)
                meta[field] = {}
                self.assert_rejected("Additional properties", meta)
        self.meta["evaluation"]["full_command"] = ["python3", "eval/all.py"]
        self.assert_rejected("Additional properties", self.meta)

    def test_protected_files_are_required_unique_paths(self):
        meta = copy.deepcopy(self.meta)
        del meta["protected_files"]
        self.assert_rejected("required property", meta)
        record = {"path": "eval/evaluate.py", "checksum": "0" * 64}
        values = [
            [], "eval/evaluate.py", ["eval/evaluate.py"], [None],
            [record, record],
            [record, {"path": "./eval/evaluate.py", "checksum": "0" * 64}],
            [record, {"path": "eval/evaluate.py", "checksum": "1" * 64}],
        ]
        values.extend(
            [{"path": path, "checksum": "0" * 64}]
            for path in ("", " ", "/eval/evaluate.py", "../data.json", ".")
        )
        for value in values:
            with self.subTest(protected_files=value):
                meta = copy.deepcopy(self.meta)
                meta["protected_files"] = value
                self.assert_rejected("/protected_files", meta)

    def test_evaluation_scripts_must_be_protected(self):
        self.meta["protected_files"] = [
            record for record in self.meta["protected_files"]
            if record["path"] != "eval/evaluate.py"
        ]
        self.assert_rejected("/evaluation/verify/script", self.meta)
        self.assert_rejected("/evaluation/benchmark/script", self.meta)

    def test_protected_paths_are_compared_without_project_access(self):
        self.meta["protected_files"][0]["path"] = "./eval/evaluate.py"
        self.assertEqual(validate_metadata(self.meta), [])

    def test_every_protected_file_requires_a_sha256_checksum(self):
        for index in range(len(self.meta["protected_files"])):
            with self.subTest(index=index, checksum="missing"):
                meta = copy.deepcopy(self.meta)
                del meta["protected_files"][index]["checksum"]
                self.assert_rejected(f"/protected_files/{index}", meta)
            for invalid in (
                None, 0, True, "", "a" * 63, "a" * 65,
                "A" * 64, "g" * 64, "a" * 64 + "\n", "sha256:" + "a" * 64,
            ):
                with self.subTest(index=index, checksum=invalid):
                    meta = copy.deepcopy(self.meta)
                    meta["protected_files"][index]["checksum"] = invalid
                    self.assert_rejected(f"/protected_files/{index}/checksum", meta)

    def test_case_interface_and_coverage_do_not_belong_in_metadata(self):
        for mode in ("verify", "benchmark"):
            for field, value in (
                ("case_selection", {"argument": "--case-id"}),
                ("list_cases_command", ["python3", "eval/verify.py", "--list-cases"]),
                ("supported_case_ids", ["1", "2"]),
                ("requested_case_ids", ["1"]),
                ("executed_case_ids", ["1"]),
            ):
                with self.subTest(mode=mode, field=field):
                    meta = copy.deepcopy(self.meta)
                    meta["evaluation"][mode][field] = value
                    self.assert_rejected("Additional properties", meta)

    def test_single_and_multiple_metrics(self):
        self.assertEqual(validate_metadata(self.meta), [])
        for group in ("verify", "benchmark"):
            metrics = self.meta["metrics"][group]
            name = next(iter(metrics))
            self.meta["metrics"][group] = {name: metrics[name]}
        self.assertEqual(validate_metadata(self.meta), [])

    def test_every_metric_requires_a_unit_and_direction(self):
        for group in ("verify", "benchmark"):
            for name in self.meta["metrics"][group]:
                for field in ("unit", "direction"):
                    with self.subTest(group=group, metric=name, missing=field):
                        meta = copy.deepcopy(self.meta)
                        del meta["metrics"][group][name][field]
                        self.assert_rejected(f"/metrics/{group}/{name}", meta)
                for invalid in ("higher", "", None, True, 1):
                    with self.subTest(group=group, metric=name, direction=invalid):
                        meta = copy.deepcopy(self.meta)
                        meta["metrics"][group][name]["direction"] = invalid
                        self.assert_rejected(f"/metrics/{group}/{name}/direction", meta)

    def test_metric_names_and_groups_are_nonempty(self):
        for group in ("verify", "benchmark"):
            meta = copy.deepcopy(self.meta)
            meta["metrics"][group] = {}
            self.assert_rejected(f"/metrics/{group}", meta)
            for name in ("", " "):
                meta = copy.deepcopy(self.meta)
                metric = next(iter(meta["metrics"][group].values()))
                meta["metrics"][group] = {name: metric}
                self.assert_rejected(f"/metrics/{group}", meta)

    def test_metric_results_are_not_required_or_accepted(self):
        self.assertEqual(validate_metadata(self.meta), [])
        for group in ("verify", "benchmark"):
            for field, value in (("value", 1.25), ("passed", True), ("actual", 0)):
                with self.subTest(group=group, field=field):
                    meta = copy.deepcopy(self.meta)
                    metric = next(iter(meta["metrics"][group].values()))
                    metric[field] = value
                    self.assert_rejected("Additional properties", meta)

    def test_strict_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            for content in (
                '{"key": 1, "key": 2}', '{"value": NaN}',
                '{"value": Infinity}', '{"value": 1e999}', '{',
            ):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_json(path)

    def test_cli_checks_metadata_without_running_or_requiring_evaluation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "should-not-be-created"
            for mode in ("verify", "benchmark"):
                self.meta["evaluation"][mode]["command"] = [
                    sys.executable, "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).touch()",
                    mode,
                ]
            path = root / ".autokernel/baseline_meta.json"
            path.parent.mkdir()
            path.write_text(json.dumps(self.meta), encoding="utf-8")
            command = [sys.executable, str(VALIDATOR), "--project-root", str(root)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Baseline metadata is valid", result.stdout)
            self.assertFalse(marker.exists())

            path.write_text('{"schema_version": 1}', encoding="utf-8")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("required property", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

            path.unlink()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
