#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET


NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", NS)


TASKS = [
    ("SDE_MARKET_OUTLOOK.xml", "SDE Swing Market Outlook 07:30 WIB", "07:30:00", "PT30M", "scheduler/SCHEDULE_MARKET_OUTLOOK.bat"),
    ("SDE_POST_MARKET.xml", "SDE Swing Post Market 16:30 WIB", "16:30:00", "PT60M", "scheduler/SCHEDULE_POST_MARKET.bat"),
    ("SDE_FINAL_WATCHLIST.xml", "SDE Swing Final Watchlist 18:00 WIB", "18:00:00", "PT60M", "scheduler/SCHEDULE_FINAL_WATCHLIST.bat"),
]


def node(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    child = ET.SubElement(parent, f"{{{NS}}}{tag}")
    if text is not None:
        child.text = text
    return child


def _indent(tree: ET.ElementTree) -> None:
    # ET.indent exists on supported Python versions, but keep a safe fallback.
    try:
        ET.indent(tree, space="  ")
    except AttributeError:  # pragma: no cover - compatibility fallback
        pass


def build_task(project_dir: Path, description: str, start_time: str, limit: str, bat_name: str) -> ET.ElementTree:
    # Version 1.3 is accepted by the documented Task Scheduler schema and works
    # across Windows 10/11. The UI may upgrade it after import.
    task = ET.Element(f"{{{NS}}}Task", {"version": "1.3"})

    reg = node(task, "RegistrationInfo")
    node(reg, "Description", description)

    triggers = node(task, "Triggers")
    trig = node(triggers, "CalendarTrigger")
    # Trigger children are sequence-sensitive in the Windows schema:
    # Enabled -> StartBoundary -> ScheduleByDay.
    node(trig, "Enabled", "true")
    node(trig, "StartBoundary", f"{datetime.now().date().isoformat()}T{start_time}")
    sched = node(trig, "ScheduleByDay")
    node(sched, "DaysInterval", "1")

    settings = node(task, "Settings")
    node(settings, "AllowStartOnDemand", "true")
    node(settings, "MultipleInstancesPolicy", "IgnoreNew")
    node(settings, "DisallowStartIfOnBatteries", "false")
    node(settings, "StopIfGoingOnBatteries", "false")
    node(settings, "StartWhenAvailable", "true")
    node(settings, "WakeToRun", "true")
    node(settings, "Enabled", "true")
    node(settings, "ExecutionTimeLimit", limit)

    # Context is intentionally omitted. When importing through Task Scheduler,
    # Windows assigns the account selected in the General tab. The previous
    # generator used Context="Author" without defining a matching Principal,
    # which made the XML invalid.
    actions = node(task, "Actions")
    exec_node = node(actions, "Exec")
    launcher = project_dir / bat_name
    node(exec_node, "Command", "cmd.exe")
    node(exec_node, "Arguments", f'/d /c ""{launcher}""')
    node(exec_node, "WorkingDirectory", str(project_dir))

    tree = ET.ElementTree(task)
    _indent(tree)
    return tree


def validate_generated_xml(path: Path, project_dir: Path) -> None:
    raw = path.read_bytes()
    if not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise RuntimeError(f"Task XML must use UTF-16 with BOM: {path}")
    declared = raw[:160].decode("utf-16", errors="ignore").lower()
    if "encoding='utf-16'" not in declared and 'encoding="utf-16"' not in declared:
        raise RuntimeError(f"Task XML declaration must specify UTF-16: {path}")

    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"t": NS}

    if root.get("version") != "1.3":
        raise RuntimeError(f"Unsupported task XML version in {path}")

    trigger = root.find("t:Triggers/t:CalendarTrigger", ns)
    if trigger is None:
        raise RuntimeError(f"CalendarTrigger missing in {path}")
    child_names = [child.tag.rsplit("}", 1)[-1] for child in list(trigger)]
    required_order = ["Enabled", "StartBoundary", "ScheduleByDay"]
    if child_names[:3] != required_order:
        raise RuntimeError(f"Invalid CalendarTrigger order in {path}: {child_names}")

    actions = root.find("t:Actions", ns)
    if actions is None:
        raise RuntimeError(f"Actions missing in {path}")
    context = actions.get("Context")
    if context:
        principal = root.find(f"t:Principals/t:Principal[@id='{context}']", ns)
        if principal is None:
            raise RuntimeError(f"Actions Context has no matching Principal in {path}")

    command = root.findtext("t:Actions/t:Exec/t:Command", default="", namespaces=ns)
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", default="", namespaces=ns)
    working_dir = root.findtext("t:Actions/t:Exec/t:WorkingDirectory", default="", namespaces=ns)
    if command.lower() != "cmd.exe":
        raise RuntimeError(f"Unexpected command in {path}: {command}")
    if str(project_dir) not in arguments or working_dir != str(project_dir):
        raise RuntimeError(f"Project path missing or inconsistent in {path}")

    text = path.read_text(encoding="utf-16")
    if "__PROJECT_DIR__" in text:
        raise RuntimeError(f"Placeholder remains in {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Windows Task Scheduler XML for SDE Swing")
    parser.add_argument("--project-dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--output-dir", default="scheduler/windows/generated")
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = project_dir / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    for filename, description, start_time, limit, bat in TASKS:
        launcher = project_dir / bat
        if not launcher.exists():
            raise FileNotFoundError(f"Scheduler launcher not found: {launcher}")
        tree = build_task(project_dir, description, start_time, limit, bat)
        path = output_dir / filename
        tree.write(path, encoding="utf-16", xml_declaration=True)
        validate_generated_xml(path, project_dir)
        print(path)

    print("Task Scheduler XML generated successfully.")
    print("Import the files from scheduler\\windows\\generated in Windows Task Scheduler.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
