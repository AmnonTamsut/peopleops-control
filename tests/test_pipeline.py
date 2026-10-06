"""Business behavior through PeopleOpsService; all employee data is synthetic."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from peopleops.fixtures import employee_fixture
from peopleops.pipeline import PeopleOpsService


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name)
        self.service = PeopleOpsService(self.data_dir)

    def employee_record(self, evidence: dict, employee_id: str) -> dict:
        return next(row for row in evidence["records"] if row["employee_id"] == employee_id)

    def test_independent_two_hour_overtime_worked_example(self) -> None:
        # The fixture supplies attendance, but the expected answer is worked out
        # independently: 10,000 ILS + 2 hours * 50 ILS/hour * 1.25.
        roster = employee_fixture()
        employee = next(row for row in roster if row["employee_id"] == "EMP-016")
        employee.update(base_salary_cents=1_000_000, hourly_cents=5_000)

        evidence = self.service.run("2026-09", "clean", employees=roster)

        record = self.employee_record(evidence, "EMP-016")
        self.assertEqual(record["overtime_minutes"], 120)
        self.assertEqual(record["expected_cents"], 1_012_500)

    def test_overtime_half_agora_rounds_up_once(self) -> None:
        # 30 / 60 * 4 * 1.25 = 2.5 agorot, rounded half up to 3.
        roster = employee_fixture()
        roster[1].update(base_salary_cents=1_000, hourly_cents=4)

        evidence = self.service.run("2026-09", "clean", employees=roster)

        record = self.employee_record(evidence, "EMP-002")
        self.assertEqual(record["overtime_minutes"], 30)
        self.assertEqual(record["expected_cents"], 1_003)

    def test_exact_100_ils_difference_is_allowed_but_one_agora_more_blocks(self) -> None:
        baseline = self.service.run("2026-09", "clean")
        reported = self.employee_record(baseline, "EMP-001")["gross_cents"]
        for difference in (10_000, -10_000, 10_001, -10_001):
            with self.subTest(difference=difference):
                roster = employee_fixture()
                roster[0].update(base_salary_cents=reported - difference, hourly_cents=0)
                evidence = self.service.run("2026-09", "clean", employees=roster)
                record = self.employee_record(evidence, "EMP-001")
                self.assertEqual(record["variance_cents"], difference)
                differences = [finding for finding in evidence["findings"]
                               if finding["employee_id"] == "EMP-001"
                               and finding["rule"] == "gross_difference"]
                if abs(difference) == 10_000:
                    self.assertEqual(differences, [])
                    self.assertEqual(evidence["run"]["status"], "published")
                    self.assertEqual(evidence["metrics"]["published_run_id"], evidence["run"]["id"])
                else:
                    self.assertEqual(len(differences), 1)
                    self.assertEqual(differences[0]["severity"], "critical")
                    self.assertEqual(differences[0]["impact_cents"], difference)
                    self.assertEqual(evidence["run"]["status"], "blocked")

    def test_blocked_review_retains_last_good_publication_then_correction_publishes(self) -> None:
        clean = self.service.run("2026-09", "clean")
        saved_clean = deepcopy(self.service.get_run(clean["run"]["id"]))
        self.assertEqual(clean["run"]["status"], "published")
        self.assertEqual(clean["metrics"]["headcount"], 72)

        review = self.service.run("2026-09", "review")
        self.assertEqual(review["run"]["status"], "blocked")
        self.assertGreater(review["run"]["critical_count"], 0)
        self.assertEqual(review["metrics"]["published_run_id"], clean["run"]["id"])
        dashboard = self.service.dashboard("2026-09")
        self.assertEqual(dashboard["run"]["id"], review["run"]["id"])
        self.assertEqual(dashboard["metrics"]["published_run_id"], clean["run"]["id"])
        self.assertEqual(self.service.get_run(clean["run"]["id"]), saved_clean)

        saved_review = deepcopy(self.service.get_run(review["run"]["id"]))
        corrected = self.service.run("2026-09", "corrected")
        self.assertEqual(corrected["run"]["status"], "published")
        self.assertEqual(corrected["run"]["critical_count"], 0)
        self.assertEqual(corrected["metrics"]["headcount"], 72)
        self.assertEqual(corrected["metrics"]["published_run_id"], corrected["run"]["id"])
        self.assertEqual(self.service.get_run(review["run"]["id"]), saved_review)
        self.assertEqual(self.service.get_run(clean["run"]["id"]), saved_clean)

    def test_identical_inputs_reuse_run_without_changing_audit_or_history(self) -> None:
        first = self.service.run("2026-09", "corrected")
        saved = deepcopy(self.service.get_run(first["run"]["id"]))
        history = self.service.dashboard("2026-09")["runs"]

        repeat = self.service.run("2026-09", "corrected")

        self.assertTrue(repeat["run"]["reused"])
        self.assertEqual(repeat["run"]["id"], first["run"]["id"])
        self.assertEqual(repeat["records"], first["records"])
        self.assertEqual(self.service.dashboard("2026-09")["runs"], history)
        self.assertEqual(self.service.get_run(first["run"]["id"]), saved)

    def test_changed_hr_source_creates_new_evidence_and_preserves_original(self) -> None:
        roster = employee_fixture()
        first = self.service.run("2026-09", "clean", employees=roster)
        saved = deepcopy(self.service.get_run(first["run"]["id"]))
        roster[0]["department"] = "Synthetic New Department"

        changed = self.service.run("2026-09", "clean", employees=roster)

        self.assertNotEqual(changed["run"]["id"], first["run"]["id"])
        self.assertNotEqual(changed["run"]["source_hash"], first["run"]["source_hash"])
        self.assertEqual(self.employee_record(changed, "EMP-001")["department"], "Synthetic New Department")
        self.assertEqual(self.service.get_run(first["run"]["id"]), saved)
        self.assertEqual(len(self.service.dashboard("2026-09")["runs"]), 2)

    def test_invalid_hr_values_are_quarantined_and_never_replace_publication(self) -> None:
        clean = self.service.run("2026-09", "clean")
        invalid_changes = (
            {"base_salary_cents": -1},
            {"base_salary_cents": 1.5},
            {"base_salary_cents": True},
            {"hourly_cents": "50"},
            {"hourly_cents": 100_000_001},
            {"active": "true"},
            {"employee_name": " "},
        )
        for change in invalid_changes:
            with self.subTest(change=change):
                roster = employee_fixture()
                roster[0].update(change)
                evidence = self.service.run("2026-09", "clean", employees=roster)
                self.assertEqual(evidence["run"]["status"], "blocked")
                self.assertEqual(evidence["run"]["rejected_rows"], 1)
                self.assertNotIn("EMP-001", [row["employee_id"] for row in evidence["records"]])
                invalid = [finding for finding in evidence["findings"]
                           if finding["employee_id"] == "EMP-001" and finding["rule"] == "invalid_employee"]
                self.assertEqual(len(invalid), 1)
                self.assertEqual(invalid[0]["severity"], "critical")
                self.assertTrue(invalid[0]["owner"])
                self.assertTrue(invalid[0]["remediation"])
                self.assertEqual(evidence["metrics"]["published_run_id"], clean["run"]["id"])
                self.assertEqual(evidence["run"]["accepted_rows"] + evidence["run"]["rejected_rows"],
                                 evidence["run"]["input_rows"])

    def test_duplicate_hr_key_rejects_both_rows_without_double_counting(self) -> None:
        roster = employee_fixture()
        roster.append(deepcopy(roster[0]))

        evidence = self.service.run("2026-09", "clean", employees=roster)

        self.assertEqual(evidence["run"]["status"], "blocked")
        self.assertEqual(evidence["run"]["rejected_rows"], 2)
        self.assertEqual(evidence["metrics"]["headcount"], 71)
        self.assertNotIn("EMP-001", [row["employee_id"] for row in evidence["records"]])
        duplicate = [finding for finding in evidence["findings"] if finding["rule"] == "duplicate_source_key"]
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0]["employee_id"], "EMP-001")
        self.assertIsNone(evidence["metrics"]["published_run_id"])

    def test_inactive_employee_payment_is_critical_and_excluded(self) -> None:
        roster = employee_fixture()
        roster[0]["active"] = False

        evidence = self.service.run("2026-09", "clean", employees=roster)

        self.assertEqual(evidence["run"]["status"], "blocked")
        self.assertNotIn("EMP-001", [row["employee_id"] for row in evidence["records"]])
        finding = next(row for row in evidence["findings"] if row["rule"] == "inactive_employee_payment")
        self.assertEqual(finding["employee_id"], "EMP-001")
        self.assertEqual(finding["severity"], "critical")
        self.assertGreater(finding["impact_cents"], 0)
        self.assertEqual(evidence["run"]["rejected_rows"], 1)

    def test_active_employee_without_attendance_and_payroll_blocks_publication(self) -> None:
        roster = employee_fixture()
        extra = deepcopy(roster[0])
        extra.update(employee_id="EMP-073", employee_name="Synthetic New Joiner")
        roster.append(extra)

        evidence = self.service.run("2026-09", "clean", employees=roster)

        self.assertEqual(evidence["run"]["status"], "blocked")
        missing = {finding["rule"] for finding in evidence["findings"]
                   if finding["employee_id"] == "EMP-073"}
        self.assertEqual(missing, {"missing_attendance", "missing_payroll"})
        self.assertEqual(evidence["metrics"]["headcount"], 72)
        self.assertNotIn("EMP-073", [row["employee_id"] for row in evidence["records"]])

    def test_review_fixture_exposes_broken_links_and_warning_without_duplicate_facts(self) -> None:
        evidence = self.service.run("2026-09", "review")

        rules = {finding["rule"] for finding in evidence["findings"]}
        self.assertTrue({"duplicate_source_key", "unknown_employee", "invalid_amount",
                         "missing_attendance", "missing_payroll", "inactive_employee_payment",
                         "gross_difference", "overtime_threshold"}.issubset(rules))
        warning = next(finding for finding in evidence["findings"] if finding["rule"] == "overtime_threshold")
        self.assertEqual(warning["severity"], "warning")
        self.assertEqual(warning["employee_id"], "EMP-033")
        self.assertEqual(self.employee_record(evidence, "EMP-033")["status"], "warning")
        ids = [row["employee_id"] for row in evidence["records"]]
        self.assertEqual(len(ids), len(set(ids)))
        for excluded in ("EMP-004", "EMP-010", "EMP-018", "EMP-029", "EMP-072", "EMP-999"):
            self.assertNotIn(excluded, ids)
        self.assertEqual(evidence["metrics"]["gross_cents"], sum(row["gross_cents"] for row in evidence["records"]))
        self.assertEqual(evidence["metrics"]["headcount"], sum(row["headcount"] for row in evidence["departments"]))

    def test_invalid_periods_are_rejected_without_creating_history(self) -> None:
        for period in ("2026-00", "2026-13", "2026-9", "26-09", "2026-09-01", "not-a-month", "0000-01", None, 202609):
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    self.service.run(period, "clean")
        for period in ("2026-00", "2026-13", "2026-9", "2026-09-01"):
            with self.subTest(dashboard_period=period):
                with self.assertRaises(ValueError):
                    self.service.dashboard(period)
        self.assertEqual(self.service.dashboard()["runs"], [])

    def test_invalid_scenario_or_roster_container_creates_no_audit(self) -> None:
        with self.assertRaises(ValueError):
            self.service.run("2026-09", "unknown")
        for employees in ({"employee_id": "EMP-001"}, "not-a-roster", [None], ["EMP-001"]):
            with self.subTest(employees=employees):
                with self.assertRaises(ValueError):
                    self.service.run("2026-09", "clean", employees=employees)
        roster = employee_fixture()
        roster[0]["base_salary_cents"] = float("nan")
        with self.assertRaises(ValueError):
            self.service.run("2026-09", "clean", employees=roster)
        self.assertEqual(self.service.dashboard()["runs"], [])

    def test_empty_roster_is_a_blocked_source_attempt_with_no_reconciled_pay(self) -> None:
        evidence = self.service.run("2026-09", "clean", employees=[])

        self.assertEqual(evidence["run"]["status"], "blocked")
        self.assertEqual(evidence["records"], [])
        self.assertEqual(evidence["metrics"]["headcount"], 0)
        self.assertEqual(evidence["metrics"]["gross_cents"], 0)
        self.assertIsNone(evidence["metrics"]["published_run_id"])
        self.assertTrue(all(finding["rule"] == "unknown_employee" for finding in evidence["findings"]))
        self.assertGreater(evidence["run"]["rejected_rows"], 0)

    def test_publication_and_history_are_independent_for_each_month(self) -> None:
        september = self.service.run("2026-09", "clean")
        august = self.service.run("2026-08", "clean")
        september_review = self.service.run("2026-09", "review")

        self.assertEqual(self.service.dashboard("2026-08")["metrics"]["published_run_id"], august["run"]["id"])
        self.assertEqual(self.service.dashboard("2026-09")["metrics"]["published_run_id"], september["run"]["id"])
        self.assertEqual(len(self.service.dashboard("2026-08")["runs"]), 1)
        self.assertEqual(len(self.service.dashboard("2026-09")["runs"]), 2)
        self.assertEqual(self.service.dashboard()["run"]["id"], september_review["run"]["id"])

    def test_snapshots_match_public_lineage_and_reuse_detects_tampering(self) -> None:
        evidence = self.service.run("2026-09", "clean")
        saved = deepcopy(self.service.get_run(evidence["run"]["id"]))
        for source in evidence["sources"]:
            with self.subTest(source=source["name"]):
                relative_path = Path(source["path"])
                self.assertFalse(relative_path.is_absolute())
                self.assertNotIn("..", relative_path.parts)
                path = self.data_dir / relative_path
                self.assertTrue(path.is_file())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source["sha256"])
        employees_source = next(source for source in evidence["sources"] if source["name"] == "employees")
        employees_file = self.data_dir / employees_source["path"]
        self.assertEqual(len(json.loads(employees_file.read_text(encoding="utf-8"))), employees_source["rows"])
        employees_file.write_text("[]", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "snapshot integrity"):
            self.service.run("2026-09", "clean")

        self.assertEqual(employees_file.read_text(encoding="utf-8"), "[]")
        self.assertEqual(self.service.get_run(evidence["run"]["id"]), saved)
        self.assertEqual(len(self.service.dashboard("2026-09")["runs"]), 1)
        self.assertEqual(self.service.dashboard("2026-09")["metrics"]["published_run_id"], evidence["run"]["id"])

    def test_empty_dashboard_and_unknown_run_have_no_invented_evidence(self) -> None:
        dashboard = self.service.dashboard("2026-09")
        self.assertIsNone(dashboard["run"])
        self.assertEqual(dashboard["records"], [])
        self.assertEqual(dashboard["runs"], [])
        self.assertIsNone(dashboard["metrics"]["published_run_id"])
        self.assertIsNone(self.service.get_run("unknown-run"))


if __name__ == "__main__":
    unittest.main()
