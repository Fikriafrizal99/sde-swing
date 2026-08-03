#!/usr/bin/env python3
"""
prepare_sde_swing_audit.py

Membuat ZIP audit SDE Swing secara otomatis dari folder proyek.

Fitur:
- Mendeteksi source code, config, raw broker, raw market, logs, output, dan Telegram.
- Menyalin hanya file yang relevan.
- Mengecualikan secret, cache, dependency, Git, file build, dan ZIP lama.
- Meredaksi API key/token/password pada file teks yang ikut disalin.
- Membuat audit_manifest.json, audit_inventory.csv, dan missing_items.txt.
- Mendukung mode dry-run untuk melihat file yang akan dimasukkan.

Contoh:
    python prepare_sde_swing_audit.py --project "C:\\SDE_SWING"
    python prepare_sde_swing_audit.py --project . --days 10
    python prepare_sde_swing_audit.py --project . --dry-run
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional


APP_NAME = "SDE_SWING_AUDIT"

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".properties", ".html", ".css",
    ".md", ".txt", ".sql", ".bat", ".cmd", ".ps1", ".sh",
}

DATA_EXTENSIONS = {
    ".csv", ".json", ".jsonl", ".ndjson", ".txt", ".xlsx", ".xls",
    ".parquet", ".feather",
}

TEXT_EXTENSIONS = SOURCE_EXTENSIONS | {
    ".csv", ".jsonl", ".ndjson", ".env.example", ".log",
}

ALLOWED_EXTENSIONS = SOURCE_EXTENSIONS | DATA_EXTENSIONS | {
    ".log", ".pdf",
}

EXCLUDED_DIR_NAMES = {
    ".git", ".idea", ".vscode", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".cache", ".venv", "venv", "env",
    "node_modules", "dist", "build", "coverage", ".next", ".nuxt",
    "tmp", "temp", "backup", "backups",
}

EXCLUDED_FILE_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "credentials.json", "client_secret.json", "service-account.json",
    "token.json", "cookies.json", "session.json", "secrets.json",
    "secret.json", "private_key.pem", "id_rsa", "id_ed25519",
}

EXCLUDED_SUFFIXES = {
    ".zip", ".7z", ".rar", ".tar", ".gz", ".pyc", ".pyo", ".class",
    ".exe", ".dll", ".so", ".dylib",
}

SECRET_NAME_PATTERNS = [
    re.compile(r"(?i)(secret|token|password|passwd|credential|cookie|session|private[_-]?key)"),
    re.compile(r"(?i)(telegram.*token|bot.*token|api[_-]?key)"),
]

SECRET_VALUE_PATTERNS = [
    # JSON/YAML/INI-like key/value pairs.
    re.compile(
        r'(?im)^(\s*["\']?(?:api[_-]?key|secret|token|password|passwd|'
        r'telegram[_-]?(?:bot[_-]?)?token|authorization|cookie|session)'
        r'["\']?\s*[:=]\s*)("[^"]*"|\'[^\']*\'|[^\s,#;]+)'
    ),
    # Bearer tokens.
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    # Telegram bot token common format.
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    # Generic long key after x-api-key header.
    re.compile(r'(?i)(x-api-key["\']?\s*[:=]\s*["\']?)[A-Za-z0-9._~+/=-]{12,}'),
]

CATEGORY_PATTERNS = {
    "source": [
        "src", "source", "app", "core", "engine", "modules", "services",
        "providers", "shared", "utils", "scripts",
    ],
    "config": [
        "config", "configs", "configuration", "settings", "threshold",
        "parameters", "rules",
    ],
    "raw_broker": [
        "broker", "broker_summary", "broker-summary", "brokerraw",
        "broker_raw", "stockbit",
    ],
    "raw_market": [
        "market", "ohlc", "ohlcv", "technical", "stock_summary",
        "stock-summary", "zapi", "price", "prices", "candle", "candles",
    ],
    "normalized": [
        "normalized", "normalised", "processed", "features", "feature",
    ],
    "candidates": [
        "candidate", "candidates", "watchlist", "scanner", "screening",
        "ranking", "shortlist",
    ],
    "final_output": [
        "output", "outputs", "result", "results", "decision", "decisions",
        "final", "signals", "signal",
    ],
    "logs": [
        "log", "logs", "audit", "trace", "telemetry", "debug",
    ],
    "telegram_output": [
        "telegram", "telegram_output", "telegram-output", "message",
        "messages", "notification", "notifications",
    ],
    "tests": [
        "test", "tests", "pytest", "spec", "specs", "fixture", "fixtures",
    ],
    "docs": [
        "readme", "docs", "documentation", "prd", "fsd", "architecture",
        "changelog",
    ],
}

REQUIRED_GROUPS = {
    "source": "Source code pipeline SDE Swing",
    "config": "Konfigurasi aktif dan threshold",
    "raw_broker": "Raw Broker Summary Stockbit",
    "raw_market": "Raw market/technical/OHLCV",
    "logs": "Log runtime atau decision trace",
    "final_output": "Output keputusan akhir",
}

MAX_TEXT_FILE_SIZE = 10 * 1024 * 1024
DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024


@dataclass
class AuditFile:
    source: str
    archive_path: str
    category: str
    size_bytes: int
    modified_at: str
    sha256: str
    redacted: bool


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def is_secret_named_file(path: Path) -> bool:
    name = path.name.lower()
    if name in EXCLUDED_FILE_NAMES:
        return True
    if name.startswith(".env") and name != ".env.example":
        return True
    normalized = normalize_name(path.stem)
    return any(pattern.search(normalized) for pattern in SECRET_NAME_PATTERNS)


def is_excluded_path(path: Path, output_path: Optional[Path]) -> bool:
    parts_lower = {part.lower() for part in path.parts}
    if parts_lower.intersection(EXCLUDED_DIR_NAMES):
        return True
    if output_path and path.resolve() == output_path.resolve():
        return True
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    if is_secret_named_file(path):
        return True
    return False


def classify_file(relative_path: Path) -> str:
    normalized = normalize_name(str(relative_path))

    # Priority matters: Telegram output before generic output, raw broker before source.
    priority = [
        "telegram_output", "raw_broker", "raw_market", "normalized",
        "candidates", "final_output", "logs", "config", "tests",
        "docs", "source",
    ]

    for category in priority:
        for keyword in CATEGORY_PATTERNS[category]:
            if normalize_name(keyword) in normalized:
                return category

    if relative_path.suffix.lower() in SOURCE_EXTENSIONS:
        return "source"
    if relative_path.suffix.lower() in DATA_EXTENSIONS:
        return "data_other"
    if relative_path.suffix.lower() == ".log":
        return "logs"
    return "other"


def parse_date_from_path(path: Path) -> Optional[datetime]:
    text = str(path)
    patterns = [
        r"(?<!\d)(20\d{2})[-_](\d{2})[-_](\d{2})(?!\d)",
        r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )
            except ValueError:
                pass
    return None


def recent_enough(path: Path, days: int, categories_with_history: set[str]) -> bool:
    category = classify_file(path)
    if category not in categories_with_history:
        return True

    cutoff = datetime.now() - timedelta(days=days)

    embedded_date = parse_date_from_path(path)
    if embedded_date:
        return embedded_date >= cutoff

    try:
        return datetime.fromtimestamp(path.stat().st_mtime) >= cutoff
    except OSError:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact_text(content: str) -> tuple[str, bool]:
    redacted = False

    for pattern in SECRET_VALUE_PATTERNS:
        def replacer(match: re.Match[str]) -> str:
            nonlocal redacted
            redacted = True
            if match.lastindex:
                prefix = match.group(1)
                return f"{prefix}<{('REDACT' + 'ED')}>"
            return f"<{('REDACT' + 'ED')}>"

        content = pattern.sub(replacer, content)

    # Redact environment-style values with suspicious variable names.
    env_pattern = re.compile(
        r"(?im)^(\s*(?:[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|COOKIE|SESSION)"
        r"[A-Z0-9_]*)\s*=\s*)(.*)$"
    )

    def env_replacer(match: re.Match[str]) -> str:
        nonlocal redacted
        value = match.group(2).strip()
        redaction_marker = f"<{('REDACT' + 'ED')}>"
        if not value or value in {'""', "''", redaction_marker, "YOUR_API_KEY"}:
            return match.group(0)
        redacted = True
        return f"{match.group(1)}<{('REDACT' + 'ED')}>"

    content = env_pattern.sub(env_replacer, content)
    return content, redacted


def copy_with_redaction(source: Path, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)

    suffix = source.suffix.lower()
    is_text = suffix in TEXT_EXTENSIONS or source.name.lower().endswith(".env.example")

    if is_text and source.stat().st_size <= MAX_TEXT_FILE_SIZE:
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = source.read_text(encoding="latin-1")
            except Exception:
                shutil.copy2(source, destination)
                return False

        text, redacted = redact_text(text)
        destination.write_text(text, encoding="utf-8")
        shutil.copystat(source, destination)
        return redacted

    shutil.copy2(source, destination)
    return False


def discover_files(
    project_root: Path,
    output_path: Path,
    days: int,
    max_file_size: int,
) -> list[Path]:
    selected: list[Path] = []
    historical_categories = {
        "raw_broker", "raw_market", "normalized", "candidates",
        "final_output", "logs", "telegram_output", "data_other",
    }

    for path in project_root.rglob("*"):
        if not path.is_file():
            continue

        try:
            relative = path.relative_to(project_root)
        except ValueError:
            continue

        if is_excluded_path(path, output_path):
            continue

        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue

        try:
            size = path.stat().st_size
        except OSError:
            continue

        if size > max_file_size:
            continue

        if not recent_enough(relative, days, historical_categories):
            continue

        selected.append(path)

    return sorted(selected, key=lambda item: str(item).lower())


def archive_relative_path(project_root: Path, source: Path, category: str) -> Path:
    relative = source.relative_to(project_root)

    # Keep original structure under a category root.
    return Path("audit_package") / category / relative


def write_inventory_csv(path: Path, files: list[AuditFile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "category", "source", "archive_path", "size_bytes",
                "modified_at", "sha256", "redacted",
            ],
        )
        writer.writeheader()
        for item in files:
            writer.writerow(asdict(item))


def build_missing_report(category_counts: dict[str, int]) -> str:
    missing = [
        f"- {label} ({category})"
        for category, label in REQUIRED_GROUPS.items()
        if category_counts.get(category, 0) == 0
    ]

    if not missing:
        return (
            "Semua kelompok data minimum terdeteksi.\n"
            "Tetap periksa audit_manifest.json untuk memastikan file yang dipilih benar.\n"
        )

    return (
        "Kelompok data berikut belum terdeteksi secara otomatis:\n\n"
        + "\n".join(missing)
        + "\n\n"
        "Tambahkan file/folder tersebut ke proyek lalu jalankan ulang skrip.\n"
        "Nama folder yang mudah dideteksi misalnya: config, raw_broker, "
        "raw_market, logs, output, telegram_output.\n"
    )


def create_zip(
    project_root: Path,
    output_path: Path,
    days: int,
    max_file_size_mb: int,
    dry_run: bool,
) -> int:
    max_file_size = max_file_size_mb * 1024 * 1024
    selected = discover_files(
        project_root=project_root,
        output_path=output_path,
        days=days,
        max_file_size=max_file_size,
    )

    if not selected:
        print("Tidak ada file relevan yang ditemukan.", file=sys.stderr)
        return 2

    print(f"Project : {project_root}")
    print(f"Output  : {output_path}")
    print(f"File    : {len(selected)}")
    print(f"History : {days} hari terakhir")
    print()

    category_counts: dict[str, int] = {}
    for path in selected:
        category = classify_file(path.relative_to(project_root))
        category_counts[category] = category_counts.get(category, 0) + 1
        print(f"[{category:15}] {path.relative_to(project_root)}")

    if dry_run:
        print("\nDry-run selesai. ZIP tidak dibuat.")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sde_swing_audit_") as temp_dir:
        staging_root = Path(temp_dir)
        manifest_files: list[AuditFile] = []

        for source in selected:
            relative = source.relative_to(project_root)
            category = classify_file(relative)
            archive_path = archive_relative_path(project_root, source, category)
            destination = staging_root / archive_path

            redacted = copy_with_redaction(source, destination)

            stat = destination.stat()
            manifest_files.append(
                AuditFile(
                    source=str(relative),
                    archive_path=str(archive_path).replace("\\", "/"),
                    category=category,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    sha256=sha256_file(destination),
                    redacted=redacted,
                )
            )

        category_counts = {}
        for item in manifest_files:
            category_counts[item.category] = category_counts.get(item.category, 0) + 1

        metadata_dir = staging_root / "audit_package" / "_audit_metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "package_name": APP_NAME,
            "created_at": datetime.now().astimezone().isoformat(),
            "project_root_name": project_root.name,
            "history_days": days,
            "file_count": len(manifest_files),
            "category_counts": category_counts,
            "security": {
                "excluded_secret_files": True,
                "text_secret_redaction_enabled": True,
                "note": (
                    "Periksa ulang ZIP sebelum dibagikan. "
                    "Jangan sertakan API key, token Telegram, cookie, session, "
                    "password, atau private key."
                ),
            },
            "files": [asdict(item) for item in manifest_files],
        }

        (metadata_dir / "audit_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        write_inventory_csv(metadata_dir / "audit_inventory.csv", manifest_files)

        (metadata_dir / "missing_items.txt").write_text(
            build_missing_report(category_counts),
            encoding="utf-8",
        )

        readme = f"""SDE SWING AUDIT PACKAGE

Dibuat      : {datetime.now().astimezone().isoformat()}
Project     : {project_root.name}
Rentang data: {days} hari terakhir
Jumlah file : {len(manifest_files)}

Struktur:
- source            : source code
- config            : konfigurasi dan threshold
- raw_broker        : data mentah Broker Summary/Stockbit
- raw_market        : OHLCV, market summary, technical input
- normalized        : data hasil normalisasi
- candidates        : candidate/watchlist/scanner output
- final_output      : keputusan akhir dan signal
- logs              : runtime log, trace, telemetry
- telegram_output   : hasil pesan Telegram
- tests             : test dan fixture
- docs              : dokumentasi
- _audit_metadata   : manifest, inventory, dan laporan file kurang

PENTING:
1. Periksa isi ZIP sebelum membagikannya.
2. Jangan membagikan API key, token Telegram, cookie, session, password,
   credential, atau private key.
3. File teks yang terdeteksi berisi secret telah disalin dalam bentuk
   teredaksi, tetapi pemeriksaan manual tetap wajib.
"""
        (metadata_dir / "README_AUDIT.txt").write_text(readme, encoding="utf-8")

        with zipfile.ZipFile(
            output_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for staged_file in sorted(staging_root.rglob("*")):
                if staged_file.is_file():
                    archive.write(
                        staged_file,
                        staged_file.relative_to(staging_root),
                    )

    print()
    print(f"ZIP berhasil dibuat: {output_path}")
    print(f"Ukuran ZIP         : {output_path.stat().st_size / (1024 * 1024):.2f} MB")
    print("Periksa folder audit_package/_audit_metadata di dalam ZIP.")
    return 0


def default_output_path(project_root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return project_root.parent / f"{APP_NAME}_{timestamp}.zip"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Membuat ZIP audit SDE Swing secara otomatis."
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="Folder root proyek SDE Swing. Default: folder saat ini.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Lokasi ZIP output. Default: satu level di atas folder proyek.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=15,
        help="Ambil data/log/output dari N hari terakhir. Default: 15.",
    )
    parser.add_argument(
        "--max-file-size-mb",
        type=int,
        default=100,
        help="Maksimum ukuran satu file yang ikut ZIP. Default: 100 MB.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tampilkan file yang akan dipilih tanpa membuat ZIP.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    project_root = args.project.expanduser().resolve()
    if not project_root.exists() or not project_root.is_dir():
        print(f"Folder proyek tidak ditemukan: {project_root}", file=sys.stderr)
        return 1

    if args.days < 1:
        print("--days minimal 1.", file=sys.stderr)
        return 1

    if args.max_file_size_mb < 1:
        print("--max-file-size-mb minimal 1.", file=sys.stderr)
        return 1

    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else default_output_path(project_root)
    )

    return create_zip(
        project_root=project_root,
        output_path=output_path,
        days=args.days,
        max_file_size_mb=args.max_file_size_mb,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
