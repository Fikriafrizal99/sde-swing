from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from generate_task_scheduler_xml import NS, TASKS, build_task, validate_generated_xml


class TaskSchedulerXmlEncodingV2Tests(unittest.TestCase):
    def test_generated_xml_is_utf16_with_bom_and_valid_structure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "SDE Project Dengan Spasi"
            for _, _, _, _, launcher in TASKS:
                launcher_path = project / launcher
                launcher_path.parent.mkdir(parents=True, exist_ok=True)
                launcher_path.write_text("@echo off\n", encoding="utf-8")

            for filename, description, start_time, limit, launcher in TASKS:
                output = project / filename
                tree = build_task(project, description, start_time, limit, launcher)
                tree.write(output, encoding="utf-16", xml_declaration=True)
                validate_generated_xml(output, project)

                raw = output.read_bytes()
                self.assertTrue(raw.startswith((b"\xff\xfe", b"\xfe\xff")))
                header = raw[:160].decode("utf-16").lower()
                self.assertIn("encoding='utf-16'", header)

                root = ET.parse(output).getroot()
                ns = {"t": NS}
                self.assertEqual(root.get("version"), "1.3")
                trigger = root.find("t:Triggers/t:CalendarTrigger", ns)
                self.assertIsNotNone(trigger)
                child_names = [child.tag.rsplit("}", 1)[-1] for child in list(trigger)]
                self.assertEqual(child_names[:3], ["Enabled", "StartBoundary", "ScheduleByDay"])
                args = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
                self.assertIn(str(project), args)


if __name__ == "__main__":
    unittest.main()
