"""Normalization, duplicate detection, quality scoring, health and export tests."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from core import runs as runs_module
from core.exporters import records_to_frame, to_csv_bytes, to_excel_bytes
from core.models import InspectionReport, QualityReport, RawRecord, Record, RunResult
from core.normalize import (
    canonical_url,
    normalize_email,
    normalize_name,
    normalize_phone,
    parse_address,
)
from core.quality import build_record, dedupe, evaluate
from core.runs import compute_health


def make_record(name="Jane Smith", phone="(512) 555-0142", email="", url="https://x.example/a/jane"):
    return Record(
        agent_name=name,
        brokerage_name="Cedar & Oak Realty",
        phone=phone,
        email=email,
        city="Austin",
        state="TX",
        profile_url=url,
        source_url=url,
        collected_at="2026-11-07T00:00:00+00:00",
        extraction_method="json-ld",
        adapter_id="generic_jsonld",
        phone_digits="".join(ch for ch in phone if ch.isdigit()),
    )


class NormalizationTests(unittest.TestCase):
    def test_us_phone_formats(self):
        cases = {
            "(512) 555-0142": ("(512) 555-0142", "5125550142"),
            "512.555.0142": ("(512) 555-0142", "5125550142"),
            "+1 512 555 0142": ("+1 (512) 555-0142", "15125550142"),
            "tel:+15125550142": ("+1 (512) 555-0142", "15125550142"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), expected)

    def test_phone_rejects_non_numbers(self):
        for raw in ("", None, "12345", "0000000000", "call us"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), ("", ""))

    def test_email_validation(self):
        self.assertEqual(normalize_email("Jane.Smith@CedarOak.COM "), "jane.smith@cedaroak.com")
        self.assertEqual(normalize_email("mailto:jane@cedaroak.com"), "jane@cedaroak.com")
        for bad in ("noreply@site.com", "logo@2x.png", "sentry@o123.ingest.sentry.io",
                    "user@example.com", "not-an-email", "a@b"):
            with self.subTest(bad=bad):
                self.assertEqual(normalize_email(bad), "")

    def test_name_strips_credentials(self):
        self.assertEqual(normalize_name("Jane Smith, ABR, CRS, REALTOR®"), "Jane Smith")
        self.assertEqual(normalize_name("  Agent: Marcus Lee "), "Marcus Lee")
        self.assertEqual(normalize_name("Priya Nair | Cedar & Oak"), "Priya Nair")

    def test_address_split(self):
        parsed = parse_address("3300 Bee Caves Rd, Suite 210, Austin, TX 78746")
        self.assertEqual(parsed["city"], "Austin")
        self.assertEqual(parsed["state"], "TX")
        self.assertEqual(parsed["postal_code"], "78746")
        self.assertIn("Bee Caves", parsed["street_address"])

    def test_canonical_url(self):
        self.assertEqual(
            canonical_url("https://WWW.Example.com/agents/jane/?utm_source=x"),
            "https://example.com/agents/jane",
        )


class DuplicateTests(unittest.TestCase):
    def test_same_profile_url_is_merged(self):
        first, second = make_record(), make_record()
        unique, removed = dedupe([first, second])
        self.assertEqual(len(unique), 1)
        self.assertEqual(removed, 1)
        self.assertIn("duplicate", second.quality_flags)

    def test_same_name_and_phone_is_merged(self):
        a = make_record(url="https://x.example/a/jane")
        b = make_record(url="https://x.example/b/jane")
        unique, removed = dedupe([a, b])
        self.assertEqual((len(unique), removed), (1, 1))

    def test_shared_office_phone_does_not_merge_different_agents(self):
        a = make_record(name="Jane Smith", phone="(512) 555-0100", url="https://x.example/a/jane")
        b = make_record(name="Marcus Lee", phone="(512) 555-0100", url="https://x.example/a/marcus")
        unique, removed = dedupe([a, b])
        self.assertEqual((len(unique), removed), (2, 0))


class QualityTests(unittest.TestCase):
    def test_empty_result_is_broken(self):
        report = evaluate([], 10, 0, ("agent_name", "phone", "email"))
        self.assertEqual(report.level, "broken")
        self.assertTrue(report.warnings)

    def test_low_contact_coverage_warns(self):
        records = [
            make_record(name="A", phone="(512) 555-0101", url="https://x.example/1"),
            make_record(name="B", phone="(512) 555-0102", url="https://x.example/2"),
            make_record(name="C", phone="", email="", url="https://x.example/3"),
        ]
        records[2].phone_digits = ""
        report = evaluate(records, 3, 0, ("agent_name", "phone"))
        self.assertTrue(any("phone or email" in w for w in report.warnings))
        self.assertIn(report.level, ("watch", "broken"))

    def test_healthy_run_is_ok(self):
        records = [make_record(name=f"Agent {i}", phone=f"(512) 555-{1000+i}",
                               email=f"a{i}@x.example", url=f"https://x.example/{i}") for i in range(5)]
        report = evaluate(records, 5, 0, ("agent_name", "phone", "email"))
        self.assertEqual(report.level, "ok")
        self.assertEqual(report.success_rate, 1.0)

    def test_build_record_flags_missing_contact(self):
        raw = RawRecord(fields={"agent_name": "Tom Velasquez"}, profile_url="https://x.example/tom",
                        source_url="https://x.example/team", method="heuristic")
        record = build_record(raw, ["agent_name"], "generic_html")
        self.assertIn("no contact details", record.quality_flags)


class HealthTests(unittest.TestCase):
    def _summary(self, records, signature="abc123", fill=None):
        return {
            "records": records,
            "structure_signature": signature,
            "field_fill": fill or {"phone": 0.9, "email": 0.6},
            "errors": [],
            "run_id": str(records),
        }

    def test_first_run_establishes_baseline(self):
        health = compute_health(self._summary(100), [])
        self.assertEqual(health.status, "first_run")
        self.assertIsNone(health.baseline_records)

    def test_large_record_drop_is_broken(self):
        prior = [self._summary(n) for n in (100, 98, 102)]
        health = compute_health(self._summary(20), prior)
        self.assertEqual(health.status, "broken")
        self.assertEqual(health.baseline_records, 100)
        self.assertTrue(any("fell" in r for r in health.reasons))

    def test_moderate_drop_is_watch(self):
        prior = [self._summary(100)]
        health = compute_health(self._summary(75), prior)
        self.assertEqual(health.status, "watch")

    def test_markup_change_is_flagged(self):
        prior = [self._summary(100, signature="old")]
        health = compute_health(self._summary(100, signature="new"), prior)
        self.assertTrue(health.structure_changed)
        self.assertEqual(health.status, "watch")

    def test_field_fill_collapse_is_watch(self):
        prior = [self._summary(100, fill={"email": 0.9, "phone": 0.9})]
        health = compute_health(self._summary(100, fill={"email": 0.1, "phone": 0.9}), prior)
        self.assertEqual(health.status, "watch")
        self.assertTrue(any("email" in r for r in health.reasons))

    def test_blocked_run_is_broken(self):
        prior = [self._summary(100)]
        current = self._summary(100)
        current["errors"] = ["Site refused access (HTTP 403)"]
        health = compute_health(current, prior)
        self.assertEqual(health.status, "broken")

    def test_history_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = runs_module.HISTORY_FILE
            runs_module.HISTORY_FILE = Path(tmp) / "history.jsonl"
            try:
                self.assertIsNone(runs_module.append_run({"run_id": "r1", "records": 3}))
                self.assertIsNone(runs_module.append_run({"run_id": "r2", "records": 4}))
                history = runs_module.load_history()
                self.assertEqual([e["run_id"] for e in history], ["r2", "r1"])
            finally:
                runs_module.HISTORY_FILE = original


class ExportTests(unittest.TestCase):
    def _run(self):
        records = [
            make_record(name="Jane Smith", phone="(512) 555-0142",
                        email="jane@x.example", url="https://x.example/agents/jane"),
            make_record(name="Marcus Lee", phone="(512) 555-0188",
                        email="", url="https://x.example/agents/marcus"),
        ]
        run = RunResult(
            run_id="20261107-120000-abc123",
            started_at="2026-11-07T12:00:00+00:00",
            finished_at="2026-11-07T12:01:00+00:00",
            url="https://x.example/agents",
            adapter_id="generic_jsonld",
            adapter_label="schema.org structured data",
            selected_fields=["agent_name", "brokerage_name", "phone", "email", "city", "state",
                             "profile_url", "source_url"],
            records=records,
            quality=QualityReport(attempted=2, extracted=2, unique=2, success_rate=1.0,
                                  field_fill={"phone": 1.0, "email": 0.5}, level="ok",
                                  warnings=["email is present in only 50% of records."]),
            inspection=InspectionReport(host="x.example", robots_detail="robots.txt loaded"),
            pages_fetched=1,
            profiles_attempted=2,
            elapsed_seconds=12.5,
        )
        run.health.status = "first_run"
        return run

    def test_csv_headers_and_encoding(self):
        run = self._run()
        data = to_csv_bytes(run.records, run.selected_fields)
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM keeps Excel happy")
        text = data.decode("utf-8-sig")
        self.assertIn("Agent name", text)
        self.assertIn("Jane Smith", text)
        self.assertNotIn("phone_digits", text)
        self.assertNotIn("duplicate_of", text)

    def test_frame_uses_selected_fields_only(self):
        frame = records_to_frame(self._run().records, ["agent_name", "email"])
        self.assertEqual(list(frame.columns), ["Agent name", "Public email", "Collected (UTC)",
                                               "Extraction method", "Adapter", "Quality flags"])

    def test_empty_export_does_not_crash(self):
        run = self._run()
        run.records = []
        run.quality = QualityReport(attempted=0, extracted=0, unique=0, level="broken")
        csv_bytes = to_csv_bytes([], run.selected_fields)
        self.assertIn(b"Agent name", csv_bytes)
        excel = to_excel_bytes(run, run.selected_fields)  # must not raise on zero rows
        self.assertGreater(len(excel), 0)

    def test_excel_has_records_and_summary_sheets(self):
        from openpyxl import load_workbook

        run = self._run()
        data = to_excel_bytes(run, run.selected_fields)
        workbook = load_workbook(io.BytesIO(data))
        self.assertEqual(workbook.sheetnames, ["Records", "Run Summary"])
        summary_values = [
            str(cell.value) for row in workbook["Run Summary"].iter_rows() for cell in row if cell.value
        ]
        self.assertTrue(any("Run ID" == v for v in summary_values))
        self.assertTrue(any("not been independently verified" in v for v in summary_values))


if __name__ == "__main__":
    unittest.main()
