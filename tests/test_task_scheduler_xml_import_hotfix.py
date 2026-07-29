from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from generate_task_scheduler_xml import NS, TASKS, build_task, validate_generated_xml


class TaskSchedulerXmlImportHotfixTests(unittest.TestCase):
    def test_xml_structure_is_import_compatible(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "SDE Project With Spaces"
            for _, _, _, _, launcher in TASKS:
                path = project / launcher
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("@echo off\n", encoding="utf-8")

            for filename, description, start_time, limit, launcher in TASKS:
                path = project / filename
                tree = build_task(project, description, start_time, limit, launcher)
                tree.write(path, encoding="utf-16", xml_declaration=True)
                validate_generated_xml(path, project)

                parsed = ET.parse(path)
                root = parsed.getroot()
                ns = {"t": NS}
                trigger = root.find("t:Triggers/t:CalendarTrigger", ns)
                self.assertIsNotNone(trigger)
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
