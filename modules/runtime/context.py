from __future__ import annotations

"""The single runtime context used by integrated Stage 3 jobs."""

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from modules.runtime_config import load_runtime_config
from swing_utils import PACKAGE_VERSION, make_run_id, read_json

RUNTIME_VERSION = PACKAGE_VERSION


@dataclass(frozen=True)
class RuntimePaths:
    """Central path resolver; paths are relative to the repository root."""

    root: Path

    def resolve(self, value: str | Path) -> Path:
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else self.root / path

    def output(self, category: str, trade_date: date | str | None = None) -> Path:
        path = self.resolve(Path("data/output") / category)
        if trade_date:
            path /= str(trade_date)
        return path

    def runtime(self, category: str, trade_date: date | str | None = None) -> Path:
        path = self.output(category, trade_date)
        return path


@dataclass
class RuntimeContext:
    """Context envelope shared by all jobs and their downstream artifacts.

    ``RunnerContext`` is still accepted by legacy functions.  This class is a
    deliberately dependency-light equivalent for integrations and tests; use
    :meth:`from_runner_context` when invoking an existing job.
    """

    job_name: str
    trade_date: date
    run_id: str
    config: dict[str, Any]
    scheduler_config: dict[str, Any] = field(default_factory=dict)
    config_path: Path | None = None
    data_sources_path: Path | None = None
    mode: str = "LIVE"
    started_at: datetime = field(default_factory=datetime.now)
    root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    config_hash: str = ""
    config_version: str = RUNTIME_VERSION
    calendar_config: dict[str, Any] | None = None
    _source_manager: Any = field(default=None, init=False, repr=False)

    @classmethod
    def from_runner_context(cls, ctx: Any) -> "RuntimeContext":
        config_path = Path(getattr(ctx, "config_path", "")) if getattr(ctx, "config_path", None) else None
        return cls(
            job_name=str(getattr(ctx, "job", "unknown")),
            trade_date=getattr(ctx, "trade_date"),
            run_id=str(getattr(ctx, "run_id", "")),
            config=dict(getattr(ctx, "config", {}) or {}),
            scheduler_config=dict(getattr(ctx, "scheduler_config", {}) or {}),
            calendar_config=dict(getattr(ctx, "calendar_config", {}) or {}),
            config_path=config_path,
            data_sources_path=Path("config/data_sources.json"),
            mode=str(getattr(ctx, "mode", "LIVE")),
            started_at=getattr(ctx, "started_at", datetime.now()),
            root=Path(__file__).resolve().parents[2],
            config_hash=str((getattr(ctx, "config_provenance", {}) or {}).get("config_hash", "")),
            config_version=str((getattr(ctx, "config_provenance", {}) or {}).get("config_version", PACKAGE_VERSION))
            or PACKAGE_VERSION,
        )

    @classmethod
    def create(
        cls,
        job_name: str,
        trade_date: date,
        config_path: str | Path = "config/pipeline.json",
        data_sources_path: str | Path = "config/data_sources.json",
        scheduler_config_path: str | Path = "config/scheduler.json",
        run_id: str | None = None,
        mode: str = "LIVE",
        root: Path | None = None,
    ) -> "RuntimeContext":
        project_root = root or Path(__file__).resolve().parents[2]
        pipeline_path = Path(config_path)
        if not pipeline_path.is_absolute():
            pipeline_path = project_root / pipeline_path
        scheduler_path = Path(scheduler_config_path)
        if not scheduler_path.is_absolute():
            scheduler_path = project_root / scheduler_path
        source_path = Path(data_sources_path)
        if not source_path.is_absolute():
            source_path = project_root / source_path
        config, provenance = load_runtime_config(pipeline_path, strict=True)
        scheduler = read_json(scheduler_path)
        calendar_path = Path(scheduler.get("trading_calendar", "config/trading_calendar.json"))
        if not calendar_path.is_absolute():
            calendar_path = project_root / calendar_path
        return cls(
            job_name=job_name,
            trade_date=trade_date,
            run_id=run_id or make_run_id(prefix=f"SDE-{job_name.upper()}"),
            config=config,
            scheduler_config=scheduler,
            calendar_config=read_json(calendar_path),
            config_path=pipeline_path,
            data_sources_path=source_path,
            mode=mode.upper(),
            root=project_root,
            config_hash=str(provenance.get("config_hash", "")),
            config_version=str(provenance.get("config_version", RUNTIME_VERSION)),
        )

    @property
    def paths(self) -> RuntimePaths:
        return RuntimePaths(self.root)

    @property
    def source_manager(self):
        if self._source_manager is None:
            from .data_source_manager import DataSourceManager

            self._source_manager = DataSourceManager(
                config_path=self.data_sources_path or self.paths.resolve("config/data_sources.json"),
                root=self.root,
                mode=self.mode,
                run_id=self.run_id,
                calendar_config=self.calendar_config,
                calendar_path=self.scheduler_config.get(
                    "trading_calendar", "config/trading_calendar.json"
                ),
            )
        return self._source_manager

    @property
    def provider_metadata(self) -> dict[str, Any]:
        return self.source_manager.provider_metadata()

    def artifact_metadata(self, *, snapshot_id: str = "", source: dict[str, Any] | None = None) -> dict[str, Any]:
        metadata = self.provider_metadata
        return {
            "schema_version": PACKAGE_VERSION,
            "run_id": self.run_id,
            "job_name": self.job_name,
            "trade_date": self.trade_date.isoformat(),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config_version": self.config_version or RUNTIME_VERSION,
            "snapshot_id": snapshot_id,
            "source_metadata": source or metadata,
            "config_hash": self.config_hash,
        }
