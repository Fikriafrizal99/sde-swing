from __future__ import annotations

import ast
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest

from modules.database.swing_history_db import connect
from modules.portfolio import stockbit_playwright_collector as collector
from modules.portfolio.broker_portfolio_backfill import (
    archive_backfill,
    find_latest_backfill_export,
    validate_backfill_dataframe,
)


ROOT = Path(__file__).resolve().parents[1]


def _task(symbol: str, day: str) -> dict[str, str]:
    return {
        "Symbol": symbol,
        "FROM_DATE": day,
        "TO_DATE": day,
        "TASK_KEY": f"{symbol}|{day}",
        "POSITION_ID": f"POS-{symbol}",
        "SOURCE": "OPEN_PORTFOLIO",
    }


def _write_tasks(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=list(collector.TASK_COLUMNS)).to_csv(
        path, index=False, encoding="utf-8-sig"
    )
    return path


def _payload(
    task: collector.BrokerTask,
    *,
    symbol: str | None = None,
    day: str | None = None,
    date_aliases: bool = False,
) -> dict[str, Any]:
    actual_day = day or task.to_date
    period = (
        {"from_date": actual_day, "to_date": actual_day}
        if date_aliases
        else {"from": actual_day, "to": actual_day}
    )
    return {
        "data": {
            **period,
            "broker_summary": {
                "symbol": symbol or task.symbol,
                "brokers_buy": [
                    {
                        "netbs_broker_code": "YP",
                        "type": "LOCAL",
                        "bvalv": 100,
                        "blotv": 12,
                        "bval": 110,
                        "blot": 14,
                        "freq": 5,
                        "netbs_buy_avg_price": 1_234,
                    },
                    {
                        "netbs_broker_code": "CC",
                        "investor_type": "FOREIGN",
                        "bvalv": 50,
                        "blot": 6,
                        "bval": 55,
                        "freq": 3,
                        "avg_price": 1_230,
                    },
                ],
                "brokers_sell": [
                    {
                        "netbs_broker_code": "AK",
                        "type": "LOCAL",
                        "svalv": 40,
                        "slotv": 4,
                        "sval": 45,
                        "slot": 5,
                        "freq": 4,
                        "netbs_sell_avg_price": 1_240,
                    },
                    {
                        "netbs_broker_code": "PD",
                        "investor_type": "FOREIGN",
                        "svalv": -10,
                        "slot": -2,
                        "sval": 12,
                        "freq": 2,
                        "avg_price": 1_245,
                    },
                ],
            },
            "bandar_detector": {
                "broker_accdist": "ACCUMULATION",
                "avg": {"accdist": "ACCUMULATION", "amount": 25, "percent": 2.5},
                "top3": {"accdist": "ACCUMULATION", "amount": 30, "percent": 3.0},
                "total_buyer": 2,
                "total_seller": 2,
                "value": 200,
                "volume": 20,
            },
        }
    }


def _collection(task: collector.BrokerTask, **payload_overrides: Any) -> collector.TaskCollection:
    payload = _payload(task, **payload_overrides)
    symbol = payload["data"]["broker_summary"]["symbol"]
    return collector.records_from_payload(
        payload,
        f"https://api.stockbit.com/v2.4/marketdetectors/{symbol}",
        task,
    )


def _frames(tasks: list[collector.BrokerTask]) -> dict[str, pd.DataFrame]:
    records = [_collection(task) for task in tasks]
    portfolio = pd.DataFrame(
        [dict(item.summary) for item in records], columns=list(collector.PORTFOLIO_COLUMNS)
    )
    return {
        "portfolio": portfolio,
        "summary": portfolio.loc[:, list(collector.SUMMARY_COLUMNS)].copy(),
        "raw": pd.DataFrame(
            [dict(row) for item in records for row in item.raw],
            columns=list(collector.RAW_COLUMNS),
        ),
        "status": pd.DataFrame(
            [dict(item.status) for item in records], columns=list(collector.STATUS_COLUMNS)
        ),
    }


class FakeSession:
    def __init__(self, *, failure_key: str = "", failure_code: str = "") -> None:
        self.failure_key = failure_key
        self.failure_code = failure_code
        self.collected: list[collector.BrokerTask] = []
        self.failures: list[tuple[str, str]] = []
        self.closed = False

    def collect(self, task: collector.BrokerTask) -> collector.TaskCollection:
        self.collected.append(task)
        if task.task_key == self.failure_key:
            raise collector.CollectorError(self.failure_code or "BROKER_TABLE_TIMEOUT")
        return _collection(task)

    def capture_failure(self, task: collector.BrokerTask, reason: str) -> None:
        self.failures.append((task.task_key, reason))

    def close(self) -> None:
        self.closed = True


def _factory(session: FakeSession):
    def build(profile: Path, headless: bool, timeout_ms: int, diagnostics: Path) -> FakeSession:
        assert profile
        assert isinstance(headless, bool)
        assert timeout_ms > 0
        assert diagnostics
        return session

    return build


def _enable(path: Path) -> None:
    collector.save_state(True, path)


def test_state_defaults_off_for_missing_corrupt_unreadable_or_invalid_value(
    tmp_path: Path, monkeypatch
) -> None:
    missing = tmp_path / "missing.json"
    assert collector.load_state(missing).enabled is False

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{broken", encoding="utf-8")
    assert collector.load_state(corrupt).enabled is False

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"enabled":"yes"}', encoding="utf-8")
    assert collector.load_state(invalid).enabled is False

    unreadable = tmp_path / "unreadable.json"
    unreadable.write_text('{"enabled":true}', encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    assert collector.load_state(unreadable).enabled is False


def test_toggle_on_and_off_persist_atomically(tmp_path: Path) -> None:
    state_path = tmp_path / "state" / "broker_playwright.json"
    assert collector.save_state(True, state_path).enabled is True
    assert collector.load_state(state_path).enabled is True
    assert collector.save_state(False, state_path).enabled is False
    assert collector.load_state(state_path).enabled is False
    assert not list(state_path.parent.glob(f".{state_path.name}.*.tmp"))


def test_off_and_no_tasks_paths_never_construct_playwright_session(tmp_path: Path) -> None:
    tasks_path = _write_tasks(tmp_path / "tasks.csv", [_task("BBRI", "2026-08-13")])
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("browser must not start")

    with pytest.raises(collector.CollectorError, match="PLAYWRIGHT_DISABLED"):
        collector.collect_and_publish(
            tasks_path,
            state_path=tmp_path / "missing-state.json",
            session_factory=forbidden,
        )
    assert calls == 0

    empty_tasks = _write_tasks(tmp_path / "empty.csv", [])
    _enable(tmp_path / "enabled.json")
    result = collector.collect_and_publish(
        empty_tasks,
        state_path=tmp_path / "enabled.json",
        session_factory=forbidden,
    )
    assert result == collector.CollectionResult(0, None, None, None, None)
    assert calls == 0


def test_task_reader_preserves_authoritative_symbol_and_independent_dates(tmp_path: Path) -> None:
    rows = [_task("BBRI", "2026-08-12"), _task("INDF", "2026-08-13")]
    tasks = collector.read_tasks(_write_tasks(tmp_path / "tasks.csv", rows))
    assert [(task.symbol, task.from_date, task.to_date, task.task_key) for task in tasks] == [
        ("BBRI", "2026-08-12", "2026-08-12", "BBRI|2026-08-12"),
        ("INDF", "2026-08-13", "2026-08-13", "INDF|2026-08-13"),
    ]
    assert collector.classify_task_period(tasks) == ("1D", "2026-08-12_to_2026-08-13")


def test_portfolio_reader_and_period_classifier_reject_aggregate_range(tmp_path: Path) -> None:
    ranged = _task("BBRI", "2026-08-13")
    ranged["FROM_DATE"] = "2026-08-12"
    with pytest.raises(collector.CollectorError, match="TASK_NOT_DAILY"):
        collector.read_tasks(_write_tasks(tmp_path / "range.csv", [ranged]))

    with pytest.raises(collector.CollectorError, match="PORTFOLIO_PERIOD_NOT_1D"):
        collector.classify_task_period(
            [collector.BrokerTask("BBRI", "2026-08-11", "2026-08-13", "range")]
        )


def test_task_reader_rejects_duplicate_without_opening_browser(tmp_path: Path) -> None:
    duplicated = [_task("BBRI", "2026-08-13"), _task("BBRI", "2026-08-13")]
    with pytest.raises(collector.CollectorError, match="DUPLICATE_TASK"):
        collector.read_tasks(_write_tasks(tmp_path / "duplicate.csv", duplicated))


def test_payload_transform_matches_userscript_v316_summary_raw_and_status_contract() -> None:
    task = collector.BrokerTask("BBRI", "2026-08-13", "2026-08-13", "BBRI|2026-08-13")
    result = _collection(task, date_aliases=True)
    row = result.summary
    assert list(row) == list(collector.PORTFOLIO_COLUMNS)
    assert row["TOTAL_BUY"] == 150
    assert row["TOTAL_SELL"] == 50
    assert row["NET_FLOW"] == 100
    assert row["TOP_BUYER_1"] == "YP"
    assert row["TOP_SELLER_1"] == "AK"
    assert row["BUYER_CONCENTRATION"] == 1.0
    assert row["SELLER_CONCENTRATION"] == 1.0
    assert row["TASK_KEY"] == task.task_key
    assert not any(
        column.startswith(("TOP_BUYER_", "TOP_SELLER_")) and column.endswith("_VALUE")
        for column in collector.SUMMARY_COLUMNS
    )
    validate_backfill_dataframe(pd.DataFrame([row]))

    assert len(result.raw) == 4
    assert list(result.raw[0]) == list(collector.RAW_COLUMNS)
    assert result.raw[0]["SIDE"] == "BUY"
    assert result.raw[0]["BROKER_TYPE"] == "LOCAL"
    assert result.raw[0]["NET_LOT"] == 12
    assert result.raw[0]["AVG_PRICE"] == 1_234
    assert result.raw[2]["SIDE"] == "SELL"
    assert result.raw[2]["NET_VALUE"] == -40
    assert result.raw[2]["NET_LOT"] == -4
    assert result.status == {
        "SYMBOL": "BBRI",
        "STATUS": "success",
        "BUYERS": 2,
        "SELLERS": 2,
        "FROM_DATE": "2026-08-13",
        "TO_DATE": "2026-08-13",
        "MESSAGE": "",
    }


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"symbol": "INDF"}, "SYMBOL_STATE_MISMATCH"),
        ({"day": "2026-08-12"}, "DATE_STATE_MISMATCH"),
    ],
)
def test_payload_identity_mismatch_fails_closed(overrides: dict[str, str], code: str) -> None:
    task = collector.BrokerTask("BBRI", "2026-08-13", "2026-08-13", "BBRI|2026-08-13")
    with pytest.raises(collector.CollectorError, match=code):
        _collection(task, **overrides)


def test_captured_get_request_is_replayed_per_task_and_sensitive_browser_headers_are_sanitized() -> None:
    template = collector.CapturedRequest(
        url=(
            "https://api.stockbit.com/v2.4/marketdetectors/BBRI"
            "?from=2026-08-12&to=2026-08-12&investor=all"
        ),
        method="GET",
        headers={
            "Cookie": "must-not-leave-browser",
            "Host": "api.stockbit.com",
            "Sec-Fetch-Site": "same-site",
            "Authorization": "runtime-only-token",
        },
        body_kind="none",
        body_value=None,
        transport="FETCH",
        captured_symbol="BBRI",
        captured_from_date="2026-08-12",
        captured_to_date="2026-08-12",
    )
    task = collector.BrokerTask("INDF", "2026-08-13", "2026-08-13", "INDF|2026-08-13")
    replay = collector.build_replay_request(template, task)
    parsed = urlsplit(replay.url)
    query = parse_qs(parsed.query)
    assert parsed.path.endswith("/marketdetectors/INDF")
    assert query["from"] == ["2026-08-13"]
    assert query["to"] == ["2026-08-13"]
    assert replay.method == "GET"
    assert replay.body is None
    assert "Cookie" not in replay.headers
    assert "Host" not in replay.headers
    assert not any(key.lower().startswith("sec-") for key in replay.headers)
    assert replay.headers["Authorization"] == "runtime-only-token"


def test_latest_period_is_replaced_by_exact_task_dates() -> None:
    template = collector.CapturedRequest(
        url=(
            "https://api.stockbit.com/v2.4/marketdetectors/BBCA"
            "?transaction_type=TRANSACTION_TYPE_NET"
            "&period=BROKER_SUMMARY_PERIOD_LATEST&limit=25"
        ),
        method="GET",
        headers={"Authorization": "runtime-only-token"},
        body_kind="none",
        body_value=None,
        transport="XHR",
        captured_symbol="BBCA",
        captured_from_date="2026-08-13",
        captured_to_date="2026-08-13",
    )
    task = collector.BrokerTask("BNBR", "2026-08-12", "2026-08-12", "BNBR|2026-08-12")

    replay = collector.build_replay_request(template, task)
    parsed = urlsplit(replay.url)
    query = parse_qs(parsed.query)

    assert parsed.path.endswith("/marketdetectors/BNBR")
    assert query["from"] == ["2026-08-12"]
    assert query["to"] == ["2026-08-12"]
    assert "period" not in query
    assert query["transaction_type"] == ["TRANSACTION_TYPE_NET"]
    assert query["limit"] == ["25"]


def test_selected_filter_value_is_not_reopened() -> None:
    class SelectedControl:
        def __init__(self) -> None:
            self.clicks = 0

        def count(self) -> int:
            return 1

        def nth(self, index: int) -> "SelectedControl":
            assert index == 0
            return self

        def is_visible(self) -> bool:
            return True

        def inner_text(self) -> str:
            return "All Investor"

        def get_attribute(self, name: str) -> None:
            return None

        def click(self) -> None:
            self.clicks += 1

    control = SelectedControl()

    class FakePage:
        def get_by_role(self, role: str, **kwargs: Any) -> SelectedControl:
            assert role == "button"
            return control

    session = object.__new__(collector.StockbitPlaywrightSession)
    session._page = FakePage()

    session._choose_filter("All Investor")

    assert control.clicks == 0


def test_bootstrap_captures_initial_request_without_driving_ui(monkeypatch) -> None:
    template = collector.CapturedRequest(
        url=(
            "https://api.stockbit.com/v2.4/marketdetectors/BBCA"
            "?period=BROKER_SUMMARY_PERIOD_LATEST"
        ),
        method="GET",
        headers={},
        body_kind="none",
        body_value=None,
        transport="XHR",
        captured_symbol="BBCA",
        captured_from_date="2026-08-13",
        captured_to_date="2026-08-13",
    )
    session = object.__new__(collector.StockbitPlaywrightSession)
    session._ready = False
    session._request_template = None
    calls: list[str] = []
    monkeypatch.setattr(
        session,
        "open_target",
        lambda *, require_auth: calls.append(f"open:{require_auth}"),
    )
    monkeypatch.setattr(
        session,
        "_wait_for_request_template",
        lambda: calls.append("capture") or template,
    )
    for method_name in (
        "_configure_filters",
        "_select_symbol",
        "_select_date",
        "_apply",
    ):
        monkeypatch.setattr(
            session,
            method_name,
            lambda *args, _name=method_name, **kwargs: pytest.fail(
                f"presentation control unexpectedly used: {_name}"
            ),
        )

    session._bootstrap(
        collector.BrokerTask("BNBR", "2026-08-13", "2026-08-13", "BNBR|2026-08-13")
    )

    assert calls == ["open:True", "capture"]
    assert session._request_template == template
    assert session._ready is True


def test_replay_prefers_authenticated_context_request() -> None:
    task = collector.BrokerTask("BNBR", "2026-08-13", "2026-08-13", "BNBR|2026-08-13")

    class FakeResponse:
        status = 200
        ok = True

        def __init__(self) -> None:
            self.disposed = False

        def json(self) -> dict[str, Any]:
            return _payload(task)

        def dispose(self) -> None:
            self.disposed = True

    response = FakeResponse()

    class FakeRequestContext:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def fetch(self, url: str, **kwargs: Any) -> FakeResponse:
            self.calls.append((url, kwargs))
            return response

    request_context = FakeRequestContext()

    class FakeContext:
        request = request_context

    session = object.__new__(collector.StockbitPlaywrightSession)
    session._context = FakeContext()
    session._timeout_ms = 30_000
    replay = collector.ReplayRequest(
        url=(
            "https://api.stockbit.com/v2.4/marketdetectors/BNBR"
            "?from=2026-08-13&to=2026-08-13"
        ),
        method="GET",
        headers={"Authorization": "runtime-only-token"},
        body=None,
        body_kind="none",
        preferred_transport="XHR",
        date_transport="QUERY / PATH",
    )

    payload, transport, fallback_used = session._replay(replay)

    assert payload["data"]["broker_summary"]["symbol"] == "BNBR"
    assert transport == "API_REQUEST"
    assert fallback_used is False
    assert request_context.calls == [
        (
            replay.url,
            {
                "method": "GET",
                "headers": {"Authorization": "runtime-only-token"},
                "timeout": 30_000,
                "fail_on_status_code": False,
            },
        )
    ]
    assert response.disposed is True


def test_captured_json_request_mutates_symbol_and_exact_task_period() -> None:
    template = collector.CapturedRequest(
        url="https://api.stockbit.com/v2.4/marketdetectors/BBRI",
        method="POST",
        headers={"Content-Type": "application/json", "Referer": "sensitive"},
        body_kind="json",
        body_value=json.dumps(
            {"ticker": "BBRI", "from_date": "2026-08-12", "to_date": "2026-08-12"}
        ),
        transport="XHR",
        captured_symbol="BBRI",
        captured_from_date="2026-08-12",
        captured_to_date="2026-08-12",
    )
    task = collector.BrokerTask("AKRA", "2026-08-11", "2026-08-11", "AKRA|2026-08-11")
    replay = collector.build_replay_request(template, task)
    assert urlsplit(replay.url).path.endswith("/marketdetectors/AKRA")
    assert json.loads(replay.body or "{}") == {
        "ticker": "AKRA",
        "from_date": "2026-08-11",
        "to_date": "2026-08-11",
    }
    assert replay.preferred_transport == "XHR"
    assert replay.date_transport == "JSON BODY"
    assert "Referer" not in replay.headers


def test_replay_is_restricted_to_stockbit_marketdetector_and_has_fetch_xhr_fallback() -> None:
    task = collector.BrokerTask("BBRI", "2026-08-13", "2026-08-13", "BBRI|2026-08-13")
    template = collector.CapturedRequest(
        url="https://evil.example/marketdetectors/BBRI?from=2026-08-12&to=2026-08-12",
        method="GET",
        headers={},
        body_kind="none",
        body_value=None,
        transport="FETCH",
        captured_symbol="BBRI",
        captured_from_date="2026-08-12",
        captured_to_date="2026-08-12",
    )
    with pytest.raises(collector.CollectorError, match="REQUEST_TEMPLATE_URL_REJECTED"):
        collector.build_replay_request(template, task)
    assert "nativeFetch" in collector.NETWORK_HOOK_SCRIPT
    assert "replayXhr" in collector.NETWORK_HOOK_SCRIPT
    assert "credentials: 'include'" in collector.NETWORK_HOOK_SCRIPT


def test_login_required_and_table_timeout_are_nonzero_collection_failures(tmp_path: Path) -> None:
    tasks_path = _write_tasks(tmp_path / "tasks.csv", [_task("BBRI", "2026-08-13")])
    state_path = tmp_path / "state.json"
    _enable(state_path)

    def login_required(*args, **kwargs):
        raise collector.CollectorError("LOGIN_REQUIRED")

    with pytest.raises(collector.CollectorError, match="LOGIN_REQUIRED"):
        collector.collect_and_publish(
            tasks_path,
            output_dir=tmp_path / "downloads-login",
            state_path=state_path,
            log_dir=tmp_path / "logs-login",
            session_factory=login_required,
        )
    login_events = [
        json.loads(line)
        for path in (tmp_path / "logs-login").glob("collector_*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert login_events[-1]["event"] == "COLLECTION_FAILED"
    assert login_events[-1]["reason"] == "LOGIN_REQUIRED"

    timed_out = FakeSession(failure_key="BBRI|2026-08-13", failure_code="BROKER_TABLE_TIMEOUT")
    with pytest.raises(collector.CollectorError, match="BROKER_TABLE_TIMEOUT"):
        collector.collect_and_publish(
            tasks_path,
            output_dir=tmp_path / "downloads-timeout",
            state_path=state_path,
            log_dir=tmp_path / "logs-timeout",
            session_factory=_factory(timed_out),
        )
    assert timed_out.closed is True
    assert timed_out.failures == [("BBRI|2026-08-13", "BROKER_TABLE_TIMEOUT")]


def test_one_failure_among_many_publishes_no_final_csv(tmp_path: Path) -> None:
    tasks_path = _write_tasks(
        tmp_path / "tasks.csv",
        [_task("BBRI", "2026-08-12"), _task("INDF", "2026-08-13")],
    )
    state_path = tmp_path / "state.json"
    _enable(state_path)
    session = FakeSession(failure_key="INDF|2026-08-13", failure_code="DATE_STATE_MISMATCH")
    output_dir = tmp_path / "downloads"

    with pytest.raises(collector.CollectorError, match="DATE_STATE_MISMATCH"):
        collector.collect_and_publish(
            tasks_path,
            output_dir=output_dir,
            state_path=state_path,
            log_dir=tmp_path / "logs",
            session_factory=_factory(session),
            now=datetime(2026, 8, 14, 12, 0, 0),
        )

    assert not list(output_dir.glob("*.csv"))
    assert session.closed is True


def test_full_success_publishes_four_period_explicit_csvs_and_is_importable(tmp_path: Path) -> None:
    tasks_path = _write_tasks(
        tmp_path / "tasks.csv",
        [_task("BBRI", "2026-08-13"), _task("INDF", "2026-08-13")],
    )
    state_path = tmp_path / "state.json"
    _enable(state_path)
    session = FakeSession()
    output_dir = tmp_path / "downloads"
    result = collector.collect_and_publish(
        tasks_path,
        output_dir=output_dir,
        state_path=state_path,
        profile_dir=tmp_path / "profile",
        log_dir=tmp_path / "logs",
        session_factory=_factory(session),
        now=datetime(2026, 8, 14, 12, 34, 56),
    )

    expected_portfolio = output_dir / "BROKER_PORTFOLIO_BACKFILL_SUMMARY_1D_2026-08-13.csv"
    assert result == collector.CollectionResult(
        task_count=2,
        output_path=expected_portfolio,
        summary_path=output_dir / "BROKER_SUMMARY_1D_2026-08-13.csv",
        raw_path=output_dir / "BROKER_RAW_1D_2026-08-13.csv",
        status_path=output_dir / "BROKER_STATUS_1D_2026-08-13.csv",
    )
    assert all(path.exists() for path in (
        result.output_path, result.summary_path, result.raw_path, result.status_path
    ))
    assert not list(output_dir.glob("*.zip"))
    assert find_latest_backfill_export(output_dir) == expected_portfolio
    assert not list(output_dir.glob(".*.tmp"))
    assert [task.symbol for task in session.collected] == ["BBRI", "INDF"]
    assert session.closed is True

    exported = pd.read_csv(expected_portfolio, low_memory=False)
    clean = validate_backfill_dataframe(exported)
    assert set(clean["EMITEN"]) == {"BBRI", "INDF"}
    assert list(pd.read_csv(result.summary_path).columns) == list(collector.SUMMARY_COLUMNS)
    assert list(pd.read_csv(result.raw_path).columns) == list(collector.RAW_COLUMNS)
    assert list(pd.read_csv(result.status_path).columns) == list(collector.STATUS_COLUMNS)

    conn = connect(tmp_path / "history.db")
    try:
        imported = archive_backfill(
            conn,
            exported,
            source_path=expected_portfolio,
            archive_root=tmp_path / "archive",
        )
    finally:
        conn.close()
    assert len(imported) == 1
    assert imported[0]["symbol_count"] == 2


def test_multi_date_daily_batch_uses_1d_range_label_without_aggregate_claim(tmp_path: Path) -> None:
    tasks = [
        collector.BrokerTask("BBRI", "2026-08-11", "2026-08-11", "BBRI|2026-08-11"),
        collector.BrokerTask("INDF", "2026-08-13", "2026-08-13", "INDF|2026-08-13"),
    ]
    paths = collector.artifact_paths(tmp_path, tasks)
    assert paths["summary"].name == "BROKER_SUMMARY_1D_2026-08-11_to_2026-08-13.csv"
    assert paths["portfolio"].name == (
        "BROKER_PORTFOLIO_BACKFILL_SUMMARY_1D_2026-08-11_to_2026-08-13.csv"
    )
    assert all("_3D_" not in path.name and "_5D_" not in path.name for path in paths.values())


def test_atomic_batch_interruption_rolls_back_all_final_and_staging_files(
    tmp_path: Path, monkeypatch
) -> None:
    tasks = [collector.BrokerTask("BBRI", "2026-08-13", "2026-08-13", "BBRI|2026-08-13")]
    frames = _frames(tasks)
    paths = collector.artifact_paths(tmp_path, tasks)
    real_replace = collector.os.replace

    def interrupted(source, target):
        if Path(target) == paths["portfolio"]:
            raise OSError("simulated interruption")
        return real_replace(source, target)

    monkeypatch.setattr(collector.os, "replace", interrupted)
    with pytest.raises(OSError, match="simulated interruption"):
        collector._publish_atomic_artifacts(frames, tasks, paths)
    assert not any(path.exists() for path in paths.values())
    assert not list(tmp_path.glob(".*.tmp"))


def test_existing_valid_export_set_is_never_overwritten(tmp_path: Path) -> None:
    tasks = [collector.BrokerTask("BBRI", "2026-08-13", "2026-08-13", "BBRI|2026-08-13")]
    frames = _frames(tasks)
    paths = collector.artifact_paths(tmp_path, tasks)
    paths["portfolio"].write_text("existing-valid-export", encoding="utf-8")
    with pytest.raises(collector.CollectorError, match="OUTPUT_ALREADY_EXISTS"):
        collector._publish_atomic_artifacts(frames, tasks, paths)
    assert paths["portfolio"].read_text(encoding="utf-8") == "existing-valid-export"
    assert not any(path.exists() for key, path in paths.items() if key != "portfolio")


def test_collector_has_no_database_mutator_trading_route_or_zip_output() -> None:
    source_path = ROOT / "modules/portfolio/stockbit_playwright_collector.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    identifiers = {
        node.id.lower() for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr.lower() for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    urls = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(("http://", "https://"))
    ]

    assert "sqlite3" not in imported_modules
    for forbidden in ("archive_backfill", "archive_broker", "execute", "executemany", "ddl"):
        assert forbidden not in identifiers
    assert urls == [collector.TARGET_URL]
    assert "/broker-analysis/stock" in collector.TARGET_URL
    assert all("/order" not in url.lower() and "/trading" not in url.lower() for url in urls)
    assert "trading_pin" not in identifiers
    assert "zipfile" not in imported_modules
    assert "SDE_BROKER_EXPORT_" not in source


def test_optional_dependency_and_local_auth_artifacts_are_explicitly_isolated() -> None:
    requirements = (ROOT / "requirements-playwright.txt").read_text(encoding="utf-8")
    core_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "playwright" in requirements.lower()
    # Playwright is a core dependency now because IDX disclosure uses the
    # browser fallback; the portfolio collector still keeps its install hint
    # and authenticated artifacts isolated.
    assert "playwright" in core_requirements.lower()
    assert "requirements-playwright.txt" in (ROOT / "modules/portfolio/stockbit_playwright_collector.py").read_text(encoding="utf-8")
    assert "data/state/broker_playwright.json" in ignore
    assert "data/state/playwright/stockbit/" in ignore
    assert "data/logs/broker_playwright/" in ignore


def test_menu_reuses_daily_task_generation_and_preserves_manual_fallback() -> None:
    menu = (ROOT / "maintenance/BACKFILL_PORTFOLIO_BROKER.bat").read_text(encoding="utf-8-sig")
    daily = (ROOT / "modules/portfolio/portfolio_broker_daily.py").read_text(encoding="utf-8")
    assert "[1] UPDATE HARIAN" in menu
    assert "[2] IMPORT HASIL ke database" in menu
    assert "[5] PLAYWRIGHT ON / OFF" in menu
    assert "[6] SETUP / LOGIN PLAYWRIGHT" in menu
    assert "Playwright Auto Collector : !PLAYWRIGHT_STATUS!" in menu
    assert "task-count --tasks" in menu
    assert "collect --tasks" in menu
    assert menu.index("portfolio_broker_daily.py") < menu.index("collect --tasks")
    assert "[ACTION MANUAL]" in menu
    assert "Tampermonkey: impor" in menu
    assert "Database BELUM diubah" in menu
    assert "from modules.portfolio.broker_portfolio_backfill import" in daily
    assert "build_tasks" in daily


def test_status_and_toggle_cli_do_not_load_playwright_package(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state.json"
    assert collector.main(["status", "--state", str(state), "--value"]) == 0
    assert capsys.readouterr().out.strip() == "OFF"
    assert collector.main(["enable", "--state", str(state)]) == 0
    assert collector.main(["status", "--state", str(state), "--value"]) == 0
    assert capsys.readouterr().out.strip().endswith("ON")
    assert collector.main(["disable", "--state", str(state)]) == 0
    assert collector.load_state(state).enabled is False
