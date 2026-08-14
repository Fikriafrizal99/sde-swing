from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import swing_utils
from swing_utils import atomic_csv, write_dict_rows_csv, write_json


def _temporary_files(destination: Path) -> list[Path]:
    return list(destination.parent.glob(f".{destination.name}.*.tmp"))


def test_concurrent_atomic_csv_writers_publish_one_complete_file(tmp_path: Path) -> None:
    destination = tmp_path / "same.csv"

    def publish(writer_id: int) -> None:
        atomic_csv(
            pd.DataFrame(
                {
                    "writer_id": [writer_id] * 200,
                    "sequence": list(range(200)),
                }
            ),
            destination,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(publish, range(24)))

    result = pd.read_csv(destination)
    assert len(result) == 200
    assert result["writer_id"].nunique() == 1
    assert result["sequence"].tolist() == list(range(200))
    assert _temporary_files(destination) == []


def test_concurrent_json_writers_never_publish_partial_json(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"
    write_json(destination, {"writer_id": -1, "values": list(range(500))})
    decode_errors: list[str] = []

    def publish(writer_id: int) -> None:
        write_json(
            destination,
            {"writer_id": writer_id, "values": list(range(500))},
        )

    def observe() -> None:
        for _ in range(250):
            try:
                payload = json.loads(destination.read_text(encoding="utf-8"))
                assert payload["values"] == list(range(500))
            except PermissionError:
                # Windows may briefly deny a new reader while an atomic rename
                # is in progress; this is distinct from publishing partial JSON.
                continue
            except (json.JSONDecodeError, KeyError, AssertionError) as exc:
                decode_errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=9) as pool:
        observer = pool.submit(observe)
        writers = [pool.submit(publish, writer_id) for writer_id in range(24)]
        for future in writers:
            future.result()
        observer.result()

    final = json.loads(destination.read_text(encoding="utf-8"))
    assert final["writer_id"] in range(24)
    assert final["values"] == list(range(500))
    assert decode_errors == []
    assert _temporary_files(destination) == []


@pytest.mark.parametrize("kind", ["json", "csv"])
def test_interrupted_replace_preserves_previous_artifact_and_cleans_temp(
    tmp_path: Path,
    kind: str,
) -> None:
    destination = tmp_path / f"artifact.{kind}"
    destination.write_text("ORIGINAL", encoding="utf-8")

    with patch.object(swing_utils.os, "replace", side_effect=OSError("replace interrupted")):
        with pytest.raises(OSError, match="replace interrupted"):
            if kind == "json":
                write_json(destination, {"replacement": True})
            else:
                atomic_csv(pd.DataFrame({"replacement": [True]}), destination)

    assert destination.read_text(encoding="utf-8") == "ORIGINAL"
    assert _temporary_files(destination) == []


def test_generic_writers_flush_and_fsync_before_replace(tmp_path: Path) -> None:
    json_path = tmp_path / "manifest.json"
    csv_path = tmp_path / "rows.csv"
    dict_csv_path = tmp_path / "dict_rows.csv"

    with patch.object(swing_utils.os, "fsync", wraps=os.fsync) as fsync:
        write_json(json_path, {"status": "ok"})
        atomic_csv(pd.DataFrame({"value": [1]}), csv_path)
        write_dict_rows_csv([{"value": 1}], dict_csv_path)

    assert fsync.call_count == 3
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"status": "ok"}
    assert pd.read_csv(csv_path).to_dict("records") == [{"value": 1}]
    assert pd.read_csv(dict_csv_path).to_dict("records") == [{"value": 1}]
    assert _temporary_files(json_path) == []
    assert _temporary_files(csv_path) == []
    assert _temporary_files(dict_csv_path) == []
