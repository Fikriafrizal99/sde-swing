#!/usr/bin/env python3
from __future__ import annotations

"""Generate Windows Task Scheduler XML from config/scheduler.json.

The XML is a portable/manual-import representation. For day-to-day setup,
`scheduler/Install-SdeSchedulers.ps1` is the preferred installer because it
registers the same settings for the current interactive Windows user.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", NS)


@dataclass(frozen=True)
class TaskSpec:
    filename: str
    task_name: str
    description: str
    trigger_type: str
    start_time: str
    execution_time_limit: str
    launcher: str


def node(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    child = ET.SubElement(parent, f"{{{NS}}}{tag}")
    if text is not None:
        child.text = text
    return child


def _indent(tree: ET.ElementTree) -> None:
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid scheduler config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Scheduler config is not an object: {path}")
    return payload


def _job_runtime(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    runtime = cfg.get("scheduler_runtime", {})
    if not isinstance(runtime, dict):
        return {}
    jobs = runtime.get("jobs", {})
    if not isinstance(jobs, dict):
        return {}
    value = jobs.get(name, {})
    return value if isinstance(value, dict) else {}


def task_specs(cfg: dict[str, Any]) -> tuple[TaskSpec, ...]:
    market_time = str(cfg.get("market_outlook", {}).get("time", "07:30"))
    post_time = str(cfg.get("post_market", {}).get("time", "16:30"))
    final_time = str(cfg.get("final_watchlist", {}).get("start_time", "18:00"))
    return (
        TaskSpec(
            "SDE_MARKET_OUTLOOK.xml",
            "SDE Swing Market Outlook",
            f"SDE Swing Market Outlook {market_time} WIB",
            "daily",
            market_time,
            str(_job_runtime(cfg, "market_outlook").get("execution_time_limit", "PT2H")),
            "scheduler/SCHEDULE_MARKET_OUTLOOK.bat",
        ),
        TaskSpec(
            "SDE_POST_MARKET.xml",
            "SDE Swing Post Market",
            f"SDE Swing Post Market {post_time} WIB",
            "daily",
            post_time,
            str(_job_runtime(cfg, "post_market").get("execution_time_limit", "PT2H30M")),
            "scheduler/SCHEDULE_POST_MARKET.bat",
        ),
        TaskSpec(
            "SDE_FINAL_WATCHLIST.xml",
            "SDE Swing Final Watchlist",
            f"SDE Swing Final Watchlist {final_time} WIB",
            "daily",
            final_time,
            str(_job_runtime(cfg, "final_watchlist").get("execution_time_limit", "PT3H")),
            "scheduler/SCHEDULE_FINAL_WATCHLIST.bat",
        ),
        TaskSpec(
            "SDE_IDX_DISCLOSURE_WATCHER.xml",
            "SDE Swing IDX Disclosure Watcher",
            "SDE Swing IDX Disclosure Watcher - starts at Windows logon",
            "logon",
            "",
            str(_job_runtime(cfg, "idx_disclosure").get("execution_time_limit", "PT0S")),
            "scheduler/SCHEDULE_IDX_DISCLOSURE.bat",
        ),
    )


def build_task(
    project_dir: Path,
    spec: TaskSpec,
    *,
    restart_count: int,
    restart_interval: str,
    multiple_instances_policy: str,
    start_when_available: bool,
    wake_to_run: bool,
) -> ET.ElementTree:
    task = ET.Element(f"{{{NS}}}Task", {"version": "1.3"})

    reg = node(task, "RegistrationInfo")
    node(reg, "Description", spec.description)

    triggers = node(task, "Triggers")
    if spec.trigger_type == "daily":
        trig = node(triggers, "CalendarTrigger")
        node(trig, "Enabled", "true")
        hhmmss = spec.start_time if len(spec.start_time.split(":")) == 3 else f"{spec.start_time}:00"
        node(trig, "StartBoundary", f"{datetime.now().date().isoformat()}T{hhmmss}")
        sched = node(trig, "ScheduleByDay")
        node(sched, "DaysInterval", "1")
    elif spec.trigger_type == "logon":
        trig = node(triggers, "LogonTrigger")
        node(trig, "Enabled", "true")
    else:
        raise ValueError(f"Unsupported trigger type: {spec.trigger_type}")

    settings = node(task, "Settings")
    node(settings, "AllowStartOnDemand", "true")
    node(settings, "MultipleInstancesPolicy", multiple_instances_policy)
    node(settings, "DisallowStartIfOnBatteries", "false")
    node(settings, "StopIfGoingOnBatteries", "false")
    node(settings, "StartWhenAvailable", "true" if start_when_available else "false")
    node(settings, "WakeToRun", "true" if wake_to_run else "false")
    node(settings, "Enabled", "true")
    if spec.execution_time_limit:
        node(settings, "ExecutionTimeLimit", spec.execution_time_limit)
    node(settings, "Priority", "5")
    restart = node(settings, "RestartOnFailure")
    node(restart, "Interval", restart_interval)
    node(restart, "Count", str(max(0, int(restart_count))))

    actions = node(task, "Actions")
    exec_node = node(actions, "Exec")
    launcher = project_dir / spec.launcher
    node(exec_node, "Command", "cmd.exe")
    node(exec_node, "Arguments", f'/d /c ""{launcher}""')
    node(exec_node, "WorkingDirectory", str(project_dir))

    tree = ET.ElementTree(task)
    _indent(tree)
    return tree


def validate_generated_xml(path: Path, project_dir: Path, spec: TaskSpec) -> None:
    raw = path.read_bytes()
    if not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise RuntimeError(f"Task XML must use UTF-16 with BOM: {path}")

    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"t": NS}
    if root.get("version") != "1.3":
        raise RuntimeError(f"Unsupported task XML version in {path}")

    if spec.trigger_type == "daily":
        trigger = root.find("t:Triggers/t:CalendarTrigger", ns)
        if trigger is None:
            raise RuntimeError(f"CalendarTrigger missing in {path}")
        child_names = [child.tag.rsplit("}", 1)[-1] for child in list(trigger)]
        if child_names[:3] != ["Enabled", "StartBoundary", "ScheduleByDay"]:
            raise RuntimeError(f"Invalid CalendarTrigger order in {path}: {child_names}")
    else:
        trigger = root.find("t:Triggers/t:LogonTrigger", ns)
        if trigger is None:
            raise RuntimeError(f"LogonTrigger missing in {path}")

    restart = root.find("t:Settings/t:RestartOnFailure", ns)
    if restart is None:
        raise RuntimeError(f"RestartOnFailure missing in {path}")
    if root.findtext("t:Settings/t:StartWhenAvailable", namespaces=ns) != "true":
        raise RuntimeError(f"StartWhenAvailable must be enabled in {path}")
    if root.findtext("t:Settings/t:WakeToRun", namespaces=ns) != "true":
        raise RuntimeError(f"WakeToRun must be enabled in {path}")

    command = root.findtext("t:Actions/t:Exec/t:Command", default="", namespaces=ns)
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", default="", namespaces=ns)
    working_dir = root.findtext("t:Actions/t:Exec/t:WorkingDirectory", default="", namespaces=ns)
    launcher = str(project_dir / spec.launcher)
    if command.lower() != "cmd.exe":
        raise RuntimeError(f"Unexpected command in {path}: {command}")
    if launcher not in arguments or working_dir != str(project_dir):
        raise RuntimeError(f"Project path missing or inconsistent in {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Windows Task Scheduler XML for SDE Swing")
    parser.add_argument("--project-dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--output-dir", default="scheduler/windows/generated")
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()
    config_path = Path(args.scheduler_config)
    if not config_path.is_absolute():
        config_path = project_dir / config_path
    cfg = _read_json(config_path)

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = project_dir / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime = cfg.get("scheduler_runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
    windows = runtime.get("windows_task", {})
    if not isinstance(windows, dict):
        windows = {}

    restart_count = int(windows.get("restart_count", 3))
    restart_interval = str(windows.get("restart_interval", "PT5M"))
    multiple_instances_policy = str(windows.get("multiple_instances_policy", "IgnoreNew"))
    start_when_available = bool(windows.get("start_when_available", True))
    wake_to_run = bool(windows.get("wake_to_run", True))

    specs = task_specs(cfg)
    for spec in specs:
        launcher = project_dir / spec.launcher
        if not launcher.exists():
            raise FileNotFoundError(f"Scheduler launcher not found: {launcher}")
        tree = build_task(
            project_dir,
            spec,
            restart_count=restart_count,
            restart_interval=restart_interval,
            multiple_instances_policy=multiple_instances_policy,
            start_when_available=start_when_available,
            wake_to_run=wake_to_run,
        )
        path = output_dir / spec.filename
        tree.write(path, encoding="utf-16", xml_declaration=True)
        validate_generated_xml(path, project_dir, spec)
        print(path)

    print("Task Scheduler XML generated successfully from config/scheduler.json.")
    print("Preferred installer: maintenance\\INSTALL_SCHEDULERS.bat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
