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


class TaskSchedulerXmlImportHotfixTests(unittest.TestCase):
    def test_xml_structure_is_import_compatible(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "SDE Project With Spaces"
            for spec in SPECS:
                path = project / spec.launcher
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("@echo off\n", encoding="utf-8")

            for spec in SPECS:
                path = project / spec.filename
                tree = build_task(
                    project,
                    spec,
                    restart_count=3,
                    restart_interval="PT5M",
                    multiple_instances_policy="IgnoreNew",
                    start_when_available=True,
                    wake_to_run=True,
                )
                tree.write(path, encoding="utf-16", xml_declaration=True)
                validate_generated_xml(path, project, spec)

                parsed = ET.parse(path)
                root = parsed.getroot()
                ns = {"t": NS}
                trigger_name = "CalendarTrigger" if spec.trigger_type == "daily" else "LogonTrigger"
                trigger = root.find(f"t:Triggers/t:{trigger_name}", ns)
                self.assertIsNotNone(trigger)
                if spec.trigger_type == "daily":
                    names = [child.tag.rsplit("}", 1)[-1] for child in list(trigger)]
                    self.assertEqual(names[:3], ["Enabled", "StartBoundary", "ScheduleByDay"])
                actions = root.find("t:Actions", ns)
                self.assertIsNotNone(actions)
                self.assertIsNone(actions.get("Context"))
                self.assertEqual(root.get("version"), "1.3")
                args = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
                self.assertIn(str(project), args)


if __name__ == "__main__":
    unittest.main()
