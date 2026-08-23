from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from generate_task_scheduler_xml import NS, build_task, task_specs, validate_generated_xml


SPECS = task_specs(
    {
        "market_outlook": {"time": "07:30"},
        "post_market": {"time": "16:30"},
        "final_watchlist": {"start_time": "18:00"},
    }
)


class TaskSchedulerXmlEncodingV2Tests(unittest.TestCase):
    def test_generated_xml_is_utf16_with_bom_and_valid_structure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "SDE Project Dengan Spasi"
            for spec in SPECS:
                launcher_path = project / spec.launcher
                launcher_path.parent.mkdir(parents=True, exist_ok=True)
                launcher_path.write_text("@echo off\n", encoding="utf-8")

            for spec in SPECS:
                output = project / spec.filename
                tree = build_task(
                    project,
                    spec,
                    restart_count=3,
                    restart_interval="PT5M",
                    multiple_instances_policy="IgnoreNew",
                    start_when_available=True,
                    wake_to_run=True,
                )
                tree.write(output, encoding="utf-16", xml_declaration=True)
                validate_generated_xml(output, project, spec)

                raw = output.read_bytes()
                self.assertTrue(raw.startswith((b"\xff\xfe", b"\xfe\xff")))
                header = raw[:160].decode("utf-16").lower()
                self.assertIn("encoding='utf-16'", header)

                root = ET.parse(output).getroot()
                ns = {"t": NS}
                self.assertEqual(root.get("version"), "1.3")
                trigger_name = "CalendarTrigger" if spec.trigger_type == "daily" else "LogonTrigger"
                trigger = root.find(f"t:Triggers/t:{trigger_name}", ns)
                self.assertIsNotNone(trigger)
                if spec.trigger_type == "daily":
                    child_names = [child.tag.rsplit("}", 1)[-1] for child in list(trigger)]
                    self.assertEqual(child_names[:3], ["Enabled", "StartBoundary", "ScheduleByDay"])
                args = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
                self.assertIn(str(project), args)


if __name__ == "__main__":
    unittest.main()
