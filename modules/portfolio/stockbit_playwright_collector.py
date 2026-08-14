#!/usr/bin/env python3
"""Optional read-only Stockbit Broker Summary collector.

The collector is deliberately a CSV producer only.  It reads the canonical
portfolio backfill task file, bootstraps an authenticated Stockbit Stock
Activity request, replays that read-only request per exact task, and publishes
period-explicit summary/raw/status plus an importer-compatible daily CSV.
Importing and database mutation remain owned by ``portfolio_broker_daily.py
import``.

Playwright is imported lazily so the default-OFF/manual workflow does not need
the optional package or a browser installation.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _LazyPandas:
    """Load pandas only when collection/validation actually needs it."""

    def __init__(self) -> None:
        self._module: Any | None = None

    def __getattr__(self, name: str) -> Any:
        if self._module is None:
            self._module = importlib.import_module("pandas")
        return getattr(self._module, name)


pd = _LazyPandas()


def validate_backfill_dataframe(frame: Any) -> Any:
    from modules.portfolio.broker_portfolio_backfill import (
        validate_backfill_dataframe as validate,
    )

    return validate(frame)


def atomic_csv(frame: Any, destination: Path, **kwargs: Any) -> None:
    from swing_utils import atomic_csv as write_atomic_csv

    write_atomic_csv(frame, destination, **kwargs)


def atomic_write_text(path: Path, body: str) -> None:
    from swing_utils import atomic_write_text as write_atomic_text

    write_atomic_text(path, body)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


TARGET_URL = "https://stockbit.com/broker-analysis/stock"
DEFAULT_TASKS = PROJECT_ROOT / "data/input/broker/BROKER_PORTFOLIO_BACKFILL_TASKS.csv"
DEFAULT_STATE = PROJECT_ROOT / "data/state/broker_playwright.json"
DEFAULT_PROFILE = PROJECT_ROOT / "data/state/playwright/stockbit"
DEFAULT_LOG_DIR = PROJECT_ROOT / "data/logs/broker_playwright"
COLLECTOR_VERSION = "1.1.0"
MARKETDETECTOR_PATH = "/marketdetectors/"
BACKFILL_PREFIX = "BROKER_PORTFOLIO_BACKFILL_SUMMARY_"

TASK_COLUMNS = ("Symbol", "FROM_DATE", "TO_DATE", "TASK_KEY", "POSITION_ID", "SOURCE")
SUMMARY_COLUMNS = (
    "FROM_DATE",
    "TO_DATE",
    "EMITEN",
    "TOTAL_BUY",
    "TOTAL_SELL",
    "NET_FLOW",
    "TOP_BUYER_1",
    "TOP_BUYER_2",
    "TOP_BUYER_3",
    "TOP_SELLER_1",
    "TOP_SELLER_2",
    "TOP_SELLER_3",
    "BUYER_CONCENTRATION",
    "SELLER_CONCENTRATION",
    "BROKER_ACCDIST",
    "AVG_ACCDIST",
    "AVG_AMOUNT",
    "AVG_PERCENT",
    "TOP3_ACCDIST",
    "TOP3_AMOUNT",
    "TOP3_PERCENT",
    "TOTAL_BUYER_COUNT",
    "TOTAL_SELLER_COUNT",
    "TOTAL_VALUE",
    "TOTAL_VOLUME",
)
PORTFOLIO_COLUMNS = (
    *SUMMARY_COLUMNS,
    "TASK_KEY",
)
RAW_COLUMNS = (
    "SYMBOL",
    "FROM_DATE",
    "TO_DATE",
    "SIDE",
    "RANK",
    "BROKER_CODE",
    "BROKER_TYPE",
    "NET_VALUE",
    "NET_LOT",
    "GROSS_VALUE",
    "GROSS_LOT",
    "FREQUENCY",
    "AVG_PRICE",
    "BROKER_ACCDIST",
    "AVG_ACCDIST",
    "AVG_AMOUNT",
    "AVG_PERCENT",
    "TOP3_ACCDIST",
    "TOP3_AMOUNT",
    "TOP3_PERCENT",
    "TOTAL_BUYER",
    "TOTAL_SELLER",
    "TOTAL_VALUE",
    "TOTAL_VOLUME",
)
STATUS_COLUMNS = (
    "SYMBOL",
    "STATUS",
    "BUYERS",
    "SELLERS",
    "FROM_DATE",
    "TO_DATE",
    "MESSAGE",
)
NUMERIC_COLUMNS = (
    "TOTAL_BUY",
    "TOTAL_SELL",
    "NET_FLOW",
    "BUYER_CONCENTRATION",
    "SELLER_CONCENTRATION",
    "AVG_AMOUNT",
    "AVG_PERCENT",
    "TOP3_AMOUNT",
    "TOP3_PERCENT",
    "TOTAL_BUYER_COUNT",
    "TOTAL_SELLER_COUNT",
    "TOTAL_VALUE",
    "TOTAL_VOLUME",
)
RAW_NUMERIC_COLUMNS = (
    "RANK",
    "NET_VALUE",
    "NET_LOT",
    "GROSS_VALUE",
    "GROSS_LOT",
    "FREQUENCY",
    "AVG_PRICE",
    "AVG_AMOUNT",
    "AVG_PERCENT",
    "TOP3_AMOUNT",
    "TOP3_PERCENT",
    "TOTAL_BUYER",
    "TOTAL_SELLER",
    "TOTAL_VALUE",
    "TOTAL_VOLUME",
)

FROM_DATE_KEYS = frozenset({
    "from", "from_date", "fromdate", "date_from", "start", "start_date",
    "startdate", "period_from", "begin", "begin_date",
})
TO_DATE_KEYS = frozenset({
    "to", "to_date", "todate", "date_to", "end", "end_date", "enddate",
    "period_to", "until", "until_date",
})
SYMBOL_KEYS = frozenset({"symbol", "emiten", "ticker", "code", "stock_code", "stockcode"})
BLOCKED_REPLAY_HEADERS = frozenset({"cookie", "host", "content-length", "origin", "referer"})

NETWORK_HOOK_SCRIPT = r"""
(() => {
  if (window.__sdeBrokerHooksInstalled) return;
  window.__sdeBrokerHooksInstalled = true;
  window.__sdeBrokerCapture = { latest: null };

  const isTarget = value => String(value || '').includes('/marketdetectors/');
  const nativeFetch = typeof window.fetch === 'function' ? window.fetch.bind(window) : null;
  const xhrProto = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
  const nativeXhr = xhrProto ? {
    open: xhrProto.open,
    send: xhrProto.send,
    setRequestHeader: xhrProto.setRequestHeader
  } : null;

  const headersObject = (...inputs) => {
    const output = {};
    for (const input of inputs) {
      try {
        const headers = new Headers(input || {});
        headers.forEach((value, key) => { output[key] = value; });
      } catch (_) {}
    }
    return output;
  };

  const cloneBody = (body, headers) => {
    if (body === null || body === undefined) return { kind: 'none', value: null };
    if (body instanceof URLSearchParams) return { kind: 'urlencoded', value: body.toString() };
    if (body instanceof FormData) {
      const entries = [];
      body.forEach((value, key) => {
        if (typeof value === 'string') entries.push([key, value]);
      });
      return { kind: 'formdata', value: JSON.stringify(entries) };
    }
    if (typeof body === 'string') {
      const contentType = Object.entries(headers || {})
        .find(([key]) => key.toLowerCase() === 'content-type')?.[1] || '';
      const trimmed = body.trim();
      if (contentType.includes('application/json') ||
          ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
           (trimmed.startsWith('[') && trimmed.endsWith(']')))) {
        return { kind: 'json', value: body };
      }
      if (contentType.includes('application/x-www-form-urlencoded')) {
        return { kind: 'urlencoded', value: body };
      }
      return { kind: 'text', value: body };
    }
    return { kind: 'unsupported', value: null };
  };

  const normalizeDate = value => {
    const match = String(value ?? '').match(/\d{4}-\d{2}-\d{2}/);
    return match ? match[0] : '';
  };

  const capture = (meta, payload) => {
    const summary = payload?.data?.broker_summary;
    if (!meta?.url || !isTarget(meta.url) || !summary || typeof summary !== 'object') return;
    const pathSymbol = String(meta.url).split('/marketdetectors/')[1]?.split(/[/?#]/)[0] || '';
    const data = payload?.data || {};
    window.__sdeBrokerCapture.latest = {
      url: meta.url,
      method: String(meta.method || 'GET').toUpperCase(),
      headers: meta.headers || {},
      body_kind: meta.body?.kind || 'none',
      body_value: meta.body?.value ?? null,
      transport: meta.transport || 'FETCH',
      captured_symbol: String(summary.symbol || pathSymbol).toUpperCase(),
      captured_from_date: normalizeDate(data.from ?? data.from_date ?? ''),
      captured_to_date: normalizeDate(data.to ?? data.to_date ?? '')
    };
  };

  if (nativeFetch) {
    window.fetch = async function(input, init = {}) {
      let meta = null;
      try {
        const request = input instanceof Request ? input : null;
        const url = new URL(
          typeof input === 'string' || input instanceof URL ? input : request?.url || '',
          location.origin
        ).href;
        const method = String(init.method || request?.method || 'GET').toUpperCase();
        const headers = headersObject(request?.headers, init.headers);
        let body = Object.prototype.hasOwnProperty.call(init, 'body') ? init.body : null;
        if (body === null && request && !['GET', 'HEAD'].includes(method)) {
          try { body = await request.clone().text(); } catch (_) {}
        }
        meta = { url, method, headers, body: cloneBody(body, headers), transport: 'FETCH' };
      } catch (_) {}
      const response = await nativeFetch(input, init);
      try {
        if (meta?.url && isTarget(meta.url)) capture(meta, await response.clone().json());
      } catch (_) {}
      return response;
    };
  }

  if (nativeXhr) {
    xhrProto.open = function(method, url, ...rest) {
      this.__sdeMethod = String(method || 'GET').toUpperCase();
      try { this.__sdeUrl = new URL(String(url || ''), location.origin).href; }
      catch (_) { this.__sdeUrl = ''; }
      this.__sdeHeaders = {};
      return nativeXhr.open.call(this, method, url, ...rest);
    };
    xhrProto.setRequestHeader = function(key, value) {
      this.__sdeHeaders = this.__sdeHeaders || {};
      this.__sdeHeaders[key] = value;
      return nativeXhr.setRequestHeader.call(this, key, value);
    };
    xhrProto.send = function(body) {
      const meta = {
        url: this.__sdeUrl,
        method: this.__sdeMethod || 'GET',
        headers: this.__sdeHeaders || {},
        body: cloneBody(body, this.__sdeHeaders || {}),
        transport: 'XHR'
      };
      this.addEventListener('load', function() {
        try {
          if (isTarget(meta.url)) capture(meta, JSON.parse(this.responseText));
        } catch (_) {}
      });
      return nativeXhr.send.call(this, body);
    };
  }

  const replayBody = request => {
    if (request.body_kind === 'formdata') {
      const form = new FormData();
      for (const [key, value] of JSON.parse(request.body || '[]')) form.append(key, value);
      return form;
    }
    return request.body ?? null;
  };

  const replayFetch = async (request, timeoutMs) => {
    if (!nativeFetch) throw new Error('FETCH_UNAVAILABLE');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const init = {
        method: request.method,
        headers: request.headers || {},
        credentials: 'include',
        cache: 'no-store',
        signal: controller.signal
      };
      if (!['GET', 'HEAD'].includes(request.method) && request.body !== null) {
        init.body = replayBody(request);
      }
      const response = await nativeFetch(request.url, init);
      if (!response.ok) throw new Error(`FETCH_HTTP_${response.status}`);
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  };

  const replayXhr = (request, timeoutMs) => new Promise((resolve, reject) => {
    if (!nativeXhr) { reject(new Error('XHR_UNAVAILABLE')); return; }
    const xhr = new XMLHttpRequest();
    try {
      nativeXhr.open.call(xhr, request.method, request.url, true);
      xhr.withCredentials = true;
      xhr.timeout = timeoutMs;
      Object.entries(request.headers || {}).forEach(([key, value]) => {
        try { nativeXhr.setRequestHeader.call(xhr, key, value); } catch (_) {}
      });
      xhr.onload = () => {
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(`XHR_HTTP_${xhr.status}`));
          return;
        }
        try { resolve(JSON.parse(xhr.responseText)); }
        catch (_) { reject(new Error('XHR_RESPONSE_NOT_JSON')); }
      };
      xhr.onerror = () => reject(new Error('XHR_NETWORK_ERROR'));
      xhr.ontimeout = () => reject(new Error('XHR_TIMEOUT'));
      xhr.onabort = () => reject(new Error('XHR_ABORTED'));
      nativeXhr.send.call(
        xhr,
        !['GET', 'HEAD'].includes(request.method) ? replayBody(request) : null
      );
    } catch (error) { reject(error); }
  });

  window.__sdeBrokerReplay = async (request, timeoutMs) => {
    const preferred = String(request.preferred_transport || 'FETCH').toUpperCase();
    const transports = preferred === 'XHR' ? ['XHR', 'FETCH'] : ['FETCH', 'XHR'];
    const failures = [];
    for (const transport of transports) {
      try {
        const payload = transport === 'XHR'
          ? await replayXhr(request, timeoutMs)
          : await replayFetch(request, timeoutMs);
        return { payload, transport, fallback_used: transport !== preferred };
      } catch (error) {
        failures.push(`${transport}:${String(error?.message || error).slice(0, 120)}`);
      }
    }
    throw new Error(`BROKER_REPLAY_FAILED:${failures.join('|')}`);
  };
})();
"""


class CollectorError(RuntimeError):
    """Fail-closed collector error with a stable machine-readable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code or "PLAYWRIGHT_FAILED").strip().upper()
        self.detail = str(detail or "").strip()
        super().__init__(self.code if not self.detail else f"{self.code}: {self.detail}")


@dataclass(frozen=True)
class CollectorState:
    enabled: bool = False
    headless: bool = True


@dataclass(frozen=True)
class BrokerTask:
    symbol: str
    from_date: str
    to_date: str
    task_key: str
    position_id: str = ""
    source: str = ""


@dataclass(frozen=True)
class CollectionResult:
    task_count: int
    output_path: Path | None
    summary_path: Path | None
    raw_path: Path | None
    status_path: Path | None


@dataclass(frozen=True)
class TaskCollection:
    summary: Mapping[str, Any]
    raw: tuple[Mapping[str, Any], ...]
    status: Mapping[str, Any]


@dataclass(frozen=True)
class CapturedRequest:
    url: str
    method: str
    headers: Mapping[str, str]
    body_kind: str
    body_value: str | None
    transport: str
    captured_symbol: str
    captured_from_date: str
    captured_to_date: str


@dataclass(frozen=True)
class ReplayRequest:
    url: str
    method: str
    headers: Mapping[str, str]
    body: str | None
    body_kind: str
    preferred_transport: str
    date_transport: str


class BrokerSummarySession(Protocol):
    def collect(self, task: BrokerTask) -> TaskCollection: ...

    def capture_failure(self, task: BrokerTask, reason: str) -> None: ...

    def close(self) -> None: ...


SessionFactory = Callable[[Path, bool, int, Path], BrokerSummarySession]


def default_downloads() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"


def _json_body(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_state(path: Path = DEFAULT_STATE) -> CollectorState:
    """Read local state; every malformed/unreadable case falls back to OFF."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return CollectorState()
    if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
        return CollectorState()
    headless = payload.get("headless", True)
    if not isinstance(headless, bool):
        headless = True
    return CollectorState(enabled=payload["enabled"], headless=headless)


def save_state(enabled: bool, path: Path = DEFAULT_STATE, *, headless: bool | None = None) -> CollectorState:
    previous = load_state(path)
    state = CollectorState(
        enabled=bool(enabled),
        headless=previous.headless if headless is None else bool(headless),
    )
    atomic_write_text(
        path,
        _json_body({"enabled": state.enabled, "headless": state.headless}),
    )
    return state


def normalize_symbol(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw.endswith(".JK"):
        raw = raw[:-3]
    match = re.fullmatch(r"[A-Z0-9]{2,12}", raw)
    return match.group(0) if match else ""


def normalize_date(value: Any) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    if not match:
        return ""
    try:
        return datetime.strptime(match.group(0), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def read_tasks(path: Path) -> list[BrokerTask]:
    if not path.exists():
        raise CollectorError("TASK_FILE_NOT_FOUND", str(path))
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except Exception as exc:
        raise CollectorError("TASK_FILE_INVALID", type(exc).__name__) from exc
    if not fieldnames:
        raise CollectorError("TASK_FILE_INVALID", "EMPTY_HEADER")
    missing = [column for column in TASK_COLUMNS[:4] if column not in fieldnames]
    if missing:
        raise CollectorError("TASK_COLUMNS_MISSING", ",".join(missing))
    if not rows:
        return []

    tasks: list[BrokerTask] = []
    seen_keys: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        symbol = normalize_symbol(row.get("Symbol"))
        from_date = normalize_date(row.get("FROM_DATE"))
        to_date = normalize_date(row.get("TO_DATE"))
        task_key = str(row.get("TASK_KEY") or "").strip()
        if not symbol:
            raise CollectorError("TASK_SYMBOL_INVALID", f"row={index + 2}")
        if not from_date or not to_date:
            raise CollectorError("TASK_DATE_INVALID", f"row={index + 2}")
        if from_date != to_date:
            raise CollectorError("TASK_NOT_DAILY", f"{symbol}|{from_date}|{to_date}")
        if not task_key:
            raise CollectorError("TASK_KEY_MISSING", f"row={index + 2}")
        pair = (symbol, to_date)
        if task_key in seen_keys or pair in seen_pairs:
            raise CollectorError("DUPLICATE_TASK", task_key)
        seen_keys.add(task_key)
        seen_pairs.add(pair)
        tasks.append(BrokerTask(
            symbol=symbol,
            from_date=from_date,
            to_date=to_date,
            task_key=task_key,
            position_id=str(row.get("POSITION_ID") or "").strip(),
            source=str(row.get("SOURCE") or "").strip(),
        ))
    return tasks


def classify_task_period(tasks: list[BrokerTask]) -> tuple[str, str]:
    """Classify this portfolio collector's only supported period: exact daily rows."""
    if not tasks:
        raise CollectorError("PERIOD_CLASSIFICATION_EMPTY")
    if any(task.from_date != task.to_date for task in tasks):
        raise CollectorError("PORTFOLIO_PERIOD_NOT_1D")
    dates = sorted({task.to_date for task in tasks})
    period_label = dates[0] if len(dates) == 1 else f"{dates[0]}_to_{dates[-1]}"
    return "1D", period_label


def _normalize_key(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def _date_parts(value: str) -> tuple[str, str, str] | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return match.groups() if match else None


def _replace_epoch_date(value: Any, old_date: str, new_date: str) -> Any:
    text = str(value or "")
    if not re.fullmatch(r"\d{10}|\d{13}", text):
        return value
    try:
        unit = 1_000 if len(text) == 10 else 1
        original = datetime.fromtimestamp(int(text) * unit / 1_000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return value
    if original.date().isoformat() != old_date:
        return value
    parts = _date_parts(new_date)
    if parts is None:
        return value
    replaced = original.replace(year=int(parts[0]), month=int(parts[1]), day=int(parts[2]))
    output = int(replaced.timestamp() * 1_000 / unit)
    return output if isinstance(value, (int, float)) else str(output)


def _replace_date_literal(value: Any, old_date: str, new_date: str) -> Any:
    if value is None or not old_date or not new_date:
        return value
    epoch = _replace_epoch_date(value, old_date, new_date)
    if str(epoch) != str(value):
        return epoch
    old_parts = _date_parts(old_date)
    new_parts = _date_parts(new_date)
    if old_parts is None or new_parts is None:
        return value
    old_y, old_m, old_d = old_parts
    new_y, new_m, new_d = new_parts
    pairs = (
        (old_date, new_date),
        (f"{old_y}{old_m}{old_d}", f"{new_y}{new_m}{new_d}"),
        (f"{old_d}/{old_m}/{old_y}", f"{new_d}/{new_m}/{new_y}"),
        (f"{old_d}-{old_m}-{old_y}", f"{new_d}-{new_m}-{new_y}"),
        (f"{old_m}/{old_d}/{old_y}", f"{new_m}/{new_d}/{new_y}"),
    )
    output = str(value)
    for old_value, new_value in pairs:
        output = output.replace(old_value, new_value)
    if isinstance(value, (int, float)) and output.isdigit():
        return int(output)
    return output


def _coerce_date_value(value: Any, old_date: str, new_date: str) -> Any:
    replaced = _replace_date_literal(value, old_date, new_date)
    if str(replaced) != str(value):
        return replaced
    text = str(value or "")
    parts = _date_parts(new_date)
    if parts is None:
        return new_date
    year, month, day = parts
    if re.fullmatch(r"\d{13}", text):
        return str(int(datetime.fromisoformat(new_date).replace(tzinfo=timezone.utc).timestamp() * 1_000))
    if re.fullmatch(r"\d{10}", text):
        return str(int(datetime.fromisoformat(new_date).replace(tzinfo=timezone.utc).timestamp()))
    if re.fullmatch(r"\d{8}", text):
        return f"{year}{month}{day}"
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        return f"{day}/{month}/{year}"
    if re.fullmatch(r"\d{2}-\d{2}-\d{4}", text):
        return f"{day}-{month}-{year}"
    return new_date


def _mutation_context(template: CapturedRequest, task: BrokerTask) -> dict[str, Any]:
    return {
        "symbol": task.symbol,
        "from_date": task.from_date,
        "to_date": task.to_date,
        "old_symbol": template.captured_symbol,
        "old_from_date": template.captured_from_date,
        "old_to_date": template.captured_to_date,
        "symbol_touched": False,
        "from_touched": False,
        "to_touched": False,
    }


def _mutate_value(value: Any, context: dict[str, Any], key_name: str = "") -> Any:
    key = _normalize_key(key_name)
    if isinstance(value, list):
        return [_mutate_value(item, context, key_name) for item in value]
    if isinstance(value, Mapping):
        return {key_: _mutate_value(child, context, key_) for key_, child in value.items()}
    if key in FROM_DATE_KEYS:
        context["from_touched"] = True
        return _coerce_date_value(value, context["old_from_date"], context["from_date"])
    if key in TO_DATE_KEYS:
        context["to_touched"] = True
        return _coerce_date_value(value, context["old_to_date"], context["to_date"])
    if key in SYMBOL_KEYS:
        context["symbol_touched"] = True
        return context["symbol"]
    if isinstance(value, str):
        output = value
        changed = _replace_date_literal(output, context["old_from_date"], context["from_date"])
        if str(changed) != output:
            output = str(changed)
            context["from_touched"] = True
        changed = _replace_date_literal(output, context["old_to_date"], context["to_date"])
        if str(changed) != output:
            output = str(changed)
            context["to_touched"] = True
        if context["old_symbol"] and output.upper() == context["old_symbol"].upper():
            output = context["symbol"]
            context["symbol_touched"] = True
        return output
    return value


def _mutate_pairs(
    pairs: list[tuple[str, str]],
    context: dict[str, Any],
) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    for key, value in pairs:
        normalized = _normalize_key(key)
        if normalized in FROM_DATE_KEYS:
            next_value = _coerce_date_value(value, context["old_from_date"], context["from_date"])
            context["from_touched"] = True
        elif normalized in TO_DATE_KEYS:
            next_value = _coerce_date_value(value, context["old_to_date"], context["to_date"])
            context["to_touched"] = True
        elif normalized in SYMBOL_KEYS:
            next_value = context["symbol"]
            context["symbol_touched"] = True
        else:
            next_value = _mutate_value(value, context, key)
        output.append((key, str(next_value)))
    return output


def _sanitize_replay_headers(headers: Mapping[str, Any], *, body_kind: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for key, value in headers.items():
        lower = str(key).lower()
        if lower in BLOCKED_REPLAY_HEADERS or lower.startswith("sec-"):
            continue
        if body_kind == "formdata" and lower == "content-type":
            continue
        output[str(key)] = str(value)
    if not any(key.lower() == "accept" for key in output):
        output["Accept"] = "application/json, text/plain, */*"
    return output


def _validate_marketdetector_url(value: str) -> None:
    parts = urlsplit(str(value or ""))
    host = (parts.hostname or "").lower()
    if (
        parts.scheme.lower() != "https"
        or not (host == "stockbit.com" or host.endswith(".stockbit.com"))
        or MARKETDETECTOR_PATH not in parts.path.lower()
    ):
        raise CollectorError("REQUEST_TEMPLATE_URL_REJECTED")


def build_replay_request(template: CapturedRequest, task: BrokerTask) -> ReplayRequest:
    """Mutate one captured Stockbit request for an exact independent task."""
    _validate_marketdetector_url(template.url)
    context = _mutation_context(template, task)
    parts = urlsplit(template.url)
    path = parts.path
    match = re.search(r"(?i)(.*/marketdetectors/)([^/]+)(.*)", path)
    if match:
        path = f"{match.group(1)}{quote(task.symbol, safe='')}{match.group(3)}"
        context["symbol_touched"] = True
    changed = _replace_date_literal(path, context["old_from_date"], task.from_date)
    if str(changed) != path:
        path = str(changed)
        context["from_touched"] = True
    changed = _replace_date_literal(path, context["old_to_date"], task.to_date)
    if str(changed) != path:
        path = str(changed)
        context["to_touched"] = True

    query = _mutate_pairs(parse_qsl(parts.query, keep_blank_values=True), context)
    method = str(template.method or "GET").upper()
    body_kind = str(template.body_kind or "none").lower()
    body: str | None = None
    if body_kind == "json":
        try:
            body_payload = json.loads(template.body_value or "{}")
        except (TypeError, ValueError) as exc:
            raise CollectorError("REQUEST_TEMPLATE_JSON_INVALID") from exc
        body = json.dumps(_mutate_value(body_payload, context), separators=(",", ":"))
        date_transport = "JSON BODY"
    elif body_kind == "urlencoded":
        pairs = parse_qsl(template.body_value or "", keep_blank_values=True)
        body = urlencode(_mutate_pairs(pairs, context))
        date_transport = "FORM BODY"
    elif body_kind == "formdata":
        try:
            entries = json.loads(template.body_value or "[]")
            pairs = [(str(key), str(value)) for key, value in entries]
        except (TypeError, ValueError) as exc:
            raise CollectorError("REQUEST_TEMPLATE_FORM_INVALID") from exc
        body = json.dumps(_mutate_pairs(pairs, context), ensure_ascii=False)
        date_transport = "FORM DATA"
    elif body_kind == "text":
        body = str(_mutate_value(template.body_value or "", context))
        date_transport = "TEXT BODY"
    elif body_kind == "none":
        date_transport = "QUERY / PATH"
    else:
        raise CollectorError("REQUEST_BODY_UNSUPPORTED", body_kind)

    if method in {"GET", "HEAD"}:
        if not context["from_touched"]:
            query.append(("from", task.from_date))
            context["from_touched"] = True
        if not context["to_touched"]:
            query.append(("to", task.to_date))
            context["to_touched"] = True
        body = None
        body_kind = "none"
    if not context["symbol_touched"]:
        raise CollectorError("REQUEST_SYMBOL_LOCATION_NOT_FOUND")
    if not context["from_touched"] or not context["to_touched"]:
        raise CollectorError("REQUEST_DATE_LOCATION_NOT_FOUND")

    url = urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), parts.fragment))
    _validate_marketdetector_url(url)
    return ReplayRequest(
        url=url,
        method=method,
        headers=_sanitize_replay_headers(template.headers, body_kind=body_kind),
        body=body,
        body_kind=body_kind,
        preferred_transport="XHR" if str(template.transport).upper() == "XHR" else "FETCH",
        date_transport=date_transport,
    )


def _safe_number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        number = float(value)
        return number if number == number else 0.0
    except (TypeError, ValueError):
        return 0.0


def _broker_code(item: Mapping[str, Any] | None) -> str:
    row = item or {}
    return str(
        row.get("netbs_broker_code")
        or row.get("broker_code")
        or row.get("code")
        or ""
    ).strip().upper()


def _broker_type(item: Mapping[str, Any] | None) -> str:
    row = item or {}
    return str(row.get("type") or row.get("investor_type") or "").strip()


def _buy_value(item: Mapping[str, Any] | None) -> float:
    row = item or {}
    return _safe_number(row.get("bvalv", row.get("net_value", row.get("bval", 0))))


def _sell_value(item: Mapping[str, Any] | None) -> float:
    row = item or {}
    value = _safe_number(row.get("svalv", row.get("net_value", row.get("sval", 0))))
    return -value if value > 0 else value


def _buy_lot(item: Mapping[str, Any] | None) -> float:
    row = item or {}
    return _safe_number(row.get("blotv", row.get("blot", 0)))


def _sell_lot(item: Mapping[str, Any] | None) -> float:
    row = item or {}
    value = _safe_number(row.get("slotv", row.get("slot", 0)))
    return -value if value > 0 else value


def _buy_average(item: Mapping[str, Any] | None) -> float:
    row = item or {}
    return _safe_number(row.get("netbs_buy_avg_price", row.get("avg_price", 0)))


def _sell_average(item: Mapping[str, Any] | None) -> float:
    row = item or {}
    return _safe_number(row.get("netbs_sell_avg_price", row.get("avg_price", 0)))


def payload_identity(payload: Mapping[str, Any], response_url: str = "") -> tuple[str, str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    summary = data.get("broker_summary") if isinstance(data.get("broker_summary"), Mapping) else {}
    url_symbol = ""
    match = re.search(r"/marketdetectors/([^/?#]+)", str(response_url), re.IGNORECASE)
    if match:
        url_symbol = match.group(1)
    symbol = normalize_symbol(summary.get("symbol") or url_symbol)
    return (
        symbol,
        normalize_date(data.get("from", data.get("from_date"))),
        normalize_date(data.get("to", data.get("to_date"))),
    )


def records_from_payload(
    payload: Mapping[str, Any],
    response_url: str,
    task: BrokerTask,
) -> TaskCollection:
    """Port the Broker Summary/raw/status semantics from userscript v3.1.6."""
    symbol, from_date, to_date = payload_identity(payload, response_url)
    if symbol != task.symbol:
        raise CollectorError("SYMBOL_STATE_MISMATCH", f"expected={task.symbol};actual={symbol or '-'}")
    if from_date != task.from_date or to_date != task.to_date:
        raise CollectorError(
            "DATE_STATE_MISMATCH",
            f"expected={task.from_date};actual={from_date or '-'}..{to_date or '-'}",
        )

    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    summary = data.get("broker_summary") if isinstance(data.get("broker_summary"), Mapping) else {}
    detector = data.get("bandar_detector") if isinstance(data.get("bandar_detector"), Mapping) else {}
    buyers = summary.get("brokers_buy")
    sellers = summary.get("brokers_sell")
    if not isinstance(buyers, list) or not isinstance(sellers, list):
        raise CollectorError("BROKER_TABLE_NOT_LOADED")

    buyer_rows = [row for row in buyers if isinstance(row, Mapping)]
    seller_rows = [row for row in sellers if isinstance(row, Mapping)]
    total_buy = sum(abs(_buy_value(row)) for row in buyer_rows)
    total_sell = sum(abs(_sell_value(row)) for row in seller_rows)
    top3_buy = sum(abs(_buy_value(row)) for row in buyer_rows[:3])
    top3_sell = sum(abs(_sell_value(row)) for row in seller_rows[:3])
    average = detector.get("avg") if isinstance(detector.get("avg"), Mapping) else {}
    top3 = detector.get("top3") if isinstance(detector.get("top3"), Mapping) else {}

    summary_row: dict[str, Any] = {
        "FROM_DATE": from_date,
        "TO_DATE": to_date,
        "EMITEN": symbol,
        "TOTAL_BUY": total_buy,
        "TOTAL_SELL": total_sell,
        "NET_FLOW": total_buy - total_sell,
        "BUYER_CONCENTRATION": top3_buy / total_buy if total_buy else 0.0,
        "SELLER_CONCENTRATION": top3_sell / total_sell if total_sell else 0.0,
        "BROKER_ACCDIST": str(detector.get("broker_accdist") or ""),
        "AVG_ACCDIST": str(average.get("accdist") or ""),
        "AVG_AMOUNT": _safe_number(average.get("amount")),
        "AVG_PERCENT": _safe_number(average.get("percent")),
        "TOP3_ACCDIST": str(top3.get("accdist") or ""),
        "TOP3_AMOUNT": _safe_number(top3.get("amount")),
        "TOP3_PERCENT": _safe_number(top3.get("percent")),
        "TOTAL_BUYER_COUNT": _safe_number(detector.get("total_buyer")),
        "TOTAL_SELLER_COUNT": _safe_number(detector.get("total_seller")),
        "TOTAL_VALUE": _safe_number(detector.get("value")),
        "TOTAL_VOLUME": _safe_number(detector.get("volume")),
        "TASK_KEY": task.task_key,
    }
    for index in range(3):
        buyer = buyer_rows[index] if index < len(buyer_rows) else None
        seller = seller_rows[index] if index < len(seller_rows) else None
        rank = index + 1
        summary_row[f"TOP_BUYER_{rank}"] = _broker_code(buyer)
        summary_row[f"TOP_SELLER_{rank}"] = _broker_code(seller)

    common = {
        "SYMBOL": symbol,
        "FROM_DATE": from_date,
        "TO_DATE": to_date,
        "BROKER_ACCDIST": str(detector.get("broker_accdist") or ""),
        "AVG_ACCDIST": str(average.get("accdist") or ""),
        "AVG_AMOUNT": _safe_number(average.get("amount")),
        "AVG_PERCENT": _safe_number(average.get("percent")),
        "TOP3_ACCDIST": str(top3.get("accdist") or ""),
        "TOP3_AMOUNT": _safe_number(top3.get("amount")),
        "TOP3_PERCENT": _safe_number(top3.get("percent")),
        "TOTAL_BUYER": _safe_number(detector.get("total_buyer")),
        "TOTAL_SELLER": _safe_number(detector.get("total_seller")),
        "TOTAL_VALUE": _safe_number(detector.get("value")),
        "TOTAL_VOLUME": _safe_number(detector.get("volume")),
    }
    raw_rows: list[dict[str, Any]] = []
    for side, rows in (("BUY", buyer_rows), ("SELL", seller_rows)):
        for index, item in enumerate(rows, start=1):
            if side == "BUY":
                net_value = _buy_value(item)
                net_lot = _buy_lot(item)
                gross_value = _safe_number(item.get("bval"))
                gross_lot = _safe_number(item.get("blot"))
                average_price = _buy_average(item)
            else:
                net_value = _sell_value(item)
                net_lot = _sell_lot(item)
                gross_value = _safe_number(item.get("sval"))
                gross_lot = _safe_number(item.get("slot"))
                average_price = _sell_average(item)
            raw_row = {
                **common,
                "SIDE": side,
                "RANK": index,
                "BROKER_CODE": _broker_code(item),
                "BROKER_TYPE": _broker_type(item),
                "NET_VALUE": net_value,
                "NET_LOT": net_lot,
                "GROSS_VALUE": gross_value,
                "GROSS_LOT": gross_lot,
                "FREQUENCY": _safe_number(item.get("freq")),
                "AVG_PRICE": average_price,
            }
            raw_rows.append({column: raw_row.get(column, "") for column in RAW_COLUMNS})

    status_row = {
        "SYMBOL": symbol,
        "STATUS": "success",
        "BUYERS": len(buyer_rows),
        "SELLERS": len(seller_rows),
        "FROM_DATE": from_date,
        "TO_DATE": to_date,
        "MESSAGE": "",
    }
    return TaskCollection(
        summary={column: summary_row.get(column, "") for column in PORTFOLIO_COLUMNS},
        raw=tuple(raw_rows),
        status={column: status_row.get(column, "") for column in STATUS_COLUMNS},
    )


def summary_from_payload(
    payload: Mapping[str, Any],
    response_url: str,
    task: BrokerTask,
) -> dict[str, Any]:
    """Compatibility helper returning the portfolio summary record only."""
    return dict(records_from_payload(payload, response_url, task).summary)


def _validate_row_for_task(row: Mapping[str, Any], task: BrokerTask) -> None:
    symbol = normalize_symbol(row.get("EMITEN"))
    from_date = normalize_date(row.get("FROM_DATE"))
    to_date = normalize_date(row.get("TO_DATE"))
    if symbol != task.symbol:
        raise CollectorError("SYMBOL_STATE_MISMATCH", f"expected={task.symbol};actual={symbol or '-'}")
    if from_date != task.from_date or to_date != task.to_date:
        raise CollectorError(
            "DATE_STATE_MISMATCH",
            f"expected={task.from_date};actual={from_date or '-'}..{to_date or '-'}",
        )
    if str(row.get("TASK_KEY") or "").strip() != task.task_key:
        raise CollectorError("TASK_KEY_MISMATCH", task.task_key)


def validate_collected_dataframe(frame: pd.DataFrame, tasks: list[BrokerTask]) -> pd.DataFrame:
    """Apply task coverage checks and the existing importer compatibility guard."""
    if len(frame) != len(tasks):
        raise CollectorError("TASK_COVERAGE_MISMATCH", f"expected={len(tasks)};actual={len(frame)}")
    missing_columns = [column for column in PORTFOLIO_COLUMNS if column not in frame.columns]
    if missing_columns:
        raise CollectorError("OUTPUT_COLUMNS_MISSING", ",".join(missing_columns))
    if frame["TASK_KEY"].astype(str).duplicated().any():
        raise CollectorError("OUTPUT_DUPLICATE_TASK")

    by_key = {task.task_key: task for task in tasks}
    actual_keys = set(frame["TASK_KEY"].astype(str))
    if actual_keys != set(by_key):
        raise CollectorError("TASK_COVERAGE_MISMATCH")
    for _, row in frame.iterrows():
        _validate_row_for_task(row, by_key[str(row["TASK_KEY"])])

    for column in NUMERIC_COLUMNS:
        parsed = pd.to_numeric(frame[column], errors="coerce")
        if parsed.isna().any():
            raise CollectorError("OUTPUT_NUMERIC_INVALID", column)

    try:
        clean = validate_backfill_dataframe(frame.loc[:, list(PORTFOLIO_COLUMNS)].copy())
    except Exception as exc:
        raise CollectorError("OUTPUT_CONTRACT_FAILED", str(exc)) from exc
    return clean.sort_values(["TO_DATE", "EMITEN", "TASK_KEY"]).reset_index(drop=True)


def validate_full_summary_dataframe(frame: pd.DataFrame, tasks: list[BrokerTask]) -> pd.DataFrame:
    missing = [column for column in SUMMARY_COLUMNS if column not in frame.columns]
    if missing:
        raise CollectorError("SUMMARY_COLUMNS_MISSING", ",".join(missing))
    if len(frame) != len(tasks):
        raise CollectorError("SUMMARY_COVERAGE_MISMATCH")
    clean = frame.loc[:, list(SUMMARY_COLUMNS)].copy()
    expected = {(task.symbol, task.from_date, task.to_date) for task in tasks}
    actual = {
        (normalize_symbol(row["EMITEN"]), normalize_date(row["FROM_DATE"]), normalize_date(row["TO_DATE"]))
        for _, row in clean.iterrows()
    }
    if actual != expected or len(actual) != len(clean):
        raise CollectorError("SUMMARY_COVERAGE_MISMATCH")
    for column in NUMERIC_COLUMNS:
        if pd.to_numeric(clean[column], errors="coerce").isna().any():
            raise CollectorError("SUMMARY_NUMERIC_INVALID", column)
    try:
        clean = validate_backfill_dataframe(clean)
    except Exception as exc:
        raise CollectorError("SUMMARY_CONTRACT_FAILED", str(exc)) from exc
    return clean.sort_values(["TO_DATE", "EMITEN"]).reset_index(drop=True)


def validate_raw_dataframe(frame: pd.DataFrame, tasks: list[BrokerTask]) -> pd.DataFrame:
    missing = [column for column in RAW_COLUMNS if column not in frame.columns]
    if missing:
        raise CollectorError("RAW_COLUMNS_MISSING", ",".join(missing))
    clean = frame.loc[:, list(RAW_COLUMNS)].copy()
    expected = {(task.symbol, task.from_date, task.to_date) for task in tasks}
    for _, row in clean.iterrows():
        identity = (
            normalize_symbol(row["SYMBOL"]),
            normalize_date(row["FROM_DATE"]),
            normalize_date(row["TO_DATE"]),
        )
        if identity not in expected:
            raise CollectorError("RAW_TASK_MISMATCH", "|".join(identity))
        if str(row["SIDE"]).upper() not in {"BUY", "SELL"}:
            raise CollectorError("RAW_SIDE_INVALID")
    for column in RAW_NUMERIC_COLUMNS:
        if not clean.empty and pd.to_numeric(clean[column], errors="coerce").isna().any():
            raise CollectorError("RAW_NUMERIC_INVALID", column)
    return clean.sort_values(["TO_DATE", "SYMBOL", "SIDE", "RANK"]).reset_index(drop=True)


def validate_status_dataframe(frame: pd.DataFrame, tasks: list[BrokerTask]) -> pd.DataFrame:
    missing = [column for column in STATUS_COLUMNS if column not in frame.columns]
    if missing:
        raise CollectorError("STATUS_COLUMNS_MISSING", ",".join(missing))
    if len(frame) != len(tasks):
        raise CollectorError("STATUS_COVERAGE_MISMATCH")
    clean = frame.loc[:, list(STATUS_COLUMNS)].copy()
    expected = {(task.symbol, task.from_date, task.to_date) for task in tasks}
    actual: set[tuple[str, str, str]] = set()
    for _, row in clean.iterrows():
        identity = (
            normalize_symbol(row["SYMBOL"]),
            normalize_date(row["FROM_DATE"]),
            normalize_date(row["TO_DATE"]),
        )
        actual.add(identity)
        if str(row["STATUS"]).strip().lower() != "success":
            raise CollectorError("STATUS_NOT_SUCCESS", "|".join(identity))
        if pd.to_numeric(pd.Series([row["BUYERS"], row["SELLERS"]]), errors="coerce").isna().any():
            raise CollectorError("STATUS_COUNT_INVALID")
    if actual != expected or len(actual) != len(clean):
        raise CollectorError("STATUS_COVERAGE_MISMATCH")
    return clean.sort_values(["TO_DATE", "SYMBOL"]).reset_index(drop=True)


def _validate_artifact_frames(
    portfolio: pd.DataFrame,
    summary: pd.DataFrame,
    raw: pd.DataFrame,
    status: pd.DataFrame,
    tasks: list[BrokerTask],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    portfolio_clean = validate_collected_dataframe(portfolio, tasks)
    summary_clean = validate_full_summary_dataframe(summary, tasks)
    raw_clean = validate_raw_dataframe(raw, tasks)
    status_clean = validate_status_dataframe(status, tasks)

    raw_counts = raw_clean.groupby(["SYMBOL", "FROM_DATE", "TO_DATE", "SIDE"]).size()
    for _, row in status_clean.iterrows():
        identity = (row["SYMBOL"], row["FROM_DATE"], row["TO_DATE"])
        if int(float(row["BUYERS"])) != int(raw_counts.get((*identity, "BUY"), 0)):
            raise CollectorError("RAW_STATUS_BUYER_COUNT_MISMATCH", "|".join(identity))
        if int(float(row["SELLERS"])) != int(raw_counts.get((*identity, "SELL"), 0)):
            raise CollectorError("RAW_STATUS_SELLER_COUNT_MISMATCH", "|".join(identity))
    return portfolio_clean, summary_clean, raw_clean, status_clean


def artifact_paths(output_dir: Path, tasks: list[BrokerTask]) -> dict[str, Path]:
    period_type, period_label = classify_task_period(tasks)
    return {
        "summary": output_dir / f"BROKER_SUMMARY_{period_type}_{period_label}.csv",
        "raw": output_dir / f"BROKER_RAW_{period_type}_{period_label}.csv",
        "status": output_dir / f"BROKER_STATUS_{period_type}_{period_label}.csv",
        "portfolio": output_dir / f"{BACKFILL_PREFIX}{period_type}_{period_label}.csv",
    }


def _publish_atomic_artifacts(
    frames: Mapping[str, pd.DataFrame],
    tasks: list[BrokerTask],
    paths: Mapping[str, Path],
) -> dict[str, Path]:
    """Stage, re-read, validate, then publish complete CSVs; portfolio is last."""
    required = ("portfolio", "summary", "raw", "status")
    if set(frames) != set(required) or set(paths) != set(required):
        raise CollectorError("ARTIFACT_SET_INVALID")
    parents = {path.parent.resolve() for path in paths.values()}
    if len(parents) != 1:
        raise CollectorError("ARTIFACT_DIRECTORY_MISMATCH")
    output_dir = ensure_dir(next(iter(paths.values())).parent)
    for path in paths.values():
        if path.exists():
            raise CollectorError("OUTPUT_ALREADY_EXISTS", str(path))

    staging = {
        key: output_dir / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        for key, path in paths.items()
    }
    published: list[tuple[Path, str]] = []
    try:
        for key in required:
            atomic_csv(frames[key], staging[key], encoding="utf-8-sig")
        disk = {
            key: pd.read_csv(staging[key], encoding="utf-8-sig", low_memory=False)
            for key in required
        }
        _validate_artifact_frames(
            disk["portfolio"], disk["summary"], disk["raw"], disk["status"], tasks
        )
        for path in paths.values():
            if path.exists():
                raise CollectorError("OUTPUT_ALREADY_EXISTS", str(path))

        # The importable portfolio artifact is deliberately the commit marker.
        for key in ("summary", "raw", "status", "portfolio"):
            digest = file_sha256(staging[key])
            os.replace(staging[key], paths[key])
            published.append((paths[key], digest))
        return dict(paths)
    except BaseException:
        for path, digest in reversed(published):
            try:
                if path.exists() and file_sha256(path) == digest:
                    path.unlink()
            except OSError:
                pass
        raise
    finally:
        for path in staging.values():
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass


def _safe_log_detail(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:400]


class DiagnosticLog:
    def __init__(self, root: Path, stamp: str) -> None:
        ensure_dir(root)
        self.path = root / f"collector_{stamp}_{uuid.uuid4().hex[:8]}.jsonl"

    def write(self, **event: Any) -> None:
        safe = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            **{
                key: _safe_log_detail(value) if key in {"detail", "reason"} else value
                for key, value in event.items()
            },
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(safe, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def collect_and_publish(
    tasks_path: Path,
    *,
    output_dir: Path | None = None,
    state_path: Path = DEFAULT_STATE,
    profile_dir: Path = DEFAULT_PROFILE,
    log_dir: Path = DEFAULT_LOG_DIR,
    headed: bool = False,
    timeout_ms: int = 30_000,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> CollectionResult:
    tasks = read_tasks(tasks_path)
    if not tasks:
        return CollectionResult(
            task_count=0,
            output_path=None,
            summary_path=None,
            raw_path=None,
            status_path=None,
        )
    period_type, period_label = classify_task_period(tasks)

    state = load_state(state_path)
    if not state.enabled:
        raise CollectorError("PLAYWRIGHT_DISABLED")
    headless = False if headed else state.headless
    current = now or datetime.now()
    stamp = current.strftime("%Y%m%d_%H%M%S")
    diagnostics = DiagnosticLog(log_dir, stamp)
    diagnostics.write(
        event="COLLECTION_START",
        producer="PLAYWRIGHT",
        collector_version=COLLECTOR_VERSION,
        task_count=len(tasks),
        period_type=period_type,
        period_label=period_label,
        headless=headless,
    )

    factory = session_factory or _real_session_factory
    session: BrokerSummarySession | None = None
    collections: list[TaskCollection] = []
    try:
        session = factory(profile_dir, headless, int(timeout_ms), log_dir)
        for task in tasks:
            started = time.monotonic()
            try:
                collected = session.collect(task)
                if not isinstance(collected, TaskCollection):
                    raise CollectorError("COLLECTION_ADAPTER_INVALID")
                _validate_row_for_task(collected.summary, task)
                collections.append(collected)
                duration = round(time.monotonic() - started, 3)
                diagnostics.write(
                    event="TASK_COMPLETE",
                    task_key=task.task_key,
                    symbol=task.symbol,
                    date=task.to_date,
                    status="OK",
                    duration_seconds=duration,
                )
                print(f"{task.symbol} | {task.to_date} | OK")
            except Exception as exc:
                error = exc if isinstance(exc, CollectorError) else CollectorError(
                    "TASK_COLLECTION_FAILED", type(exc).__name__
                )
                diagnostics.write(
                    event="TASK_COMPLETE",
                    task_key=task.task_key,
                    symbol=task.symbol,
                    date=task.to_date,
                    status="FAILED",
                    reason=error.code,
                    detail=error.detail,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                try:
                    session.capture_failure(task, error.code)
                except Exception:
                    pass
                raise error from exc
    except Exception as exc:
        error = exc if isinstance(exc, CollectorError) else CollectorError(
            "COLLECTION_FAILED", type(exc).__name__
        )
        diagnostics.write(
            event="COLLECTION_FAILED",
            status="FAILED",
            reason=error.code,
            detail=error.detail,
        )
        raise error from exc
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    try:
        portfolio_frame = pd.DataFrame(
            [dict(item.summary) for item in collections], columns=list(PORTFOLIO_COLUMNS)
        )
        summary_frame = portfolio_frame.loc[:, list(SUMMARY_COLUMNS)].copy()
        raw_frame = pd.DataFrame(
            [dict(row) for item in collections for row in item.raw], columns=list(RAW_COLUMNS)
        )
        status_frame = pd.DataFrame(
            [dict(item.status) for item in collections], columns=list(STATUS_COLUMNS)
        )
        portfolio_frame, summary_frame, raw_frame, status_frame = _validate_artifact_frames(
            portfolio_frame, summary_frame, raw_frame, status_frame, tasks
        )
        target_dir = output_dir or default_downloads()
        paths = artifact_paths(target_dir, tasks)
        published = _publish_atomic_artifacts(
            {
                "portfolio": portfolio_frame,
                "summary": summary_frame,
                "raw": raw_frame,
                "status": status_frame,
            },
            tasks,
            paths,
        )
    except Exception as exc:
        error = exc if isinstance(exc, CollectorError) else CollectorError(
            "OUTPUT_PUBLICATION_FAILED", type(exc).__name__
        )
        diagnostics.write(
            event="COLLECTION_FAILED",
            status="FAILED",
            reason=error.code,
            detail=error.detail,
        )
        raise error from exc
    diagnostics.write(
        event="COLLECTION_COMPLETE",
        status="SUCCESS",
        task_count=len(tasks),
        period_type=period_type,
        output_path=str(published["portfolio"]),
        summary_path=str(published["summary"]),
        raw_path=str(published["raw"]),
        status_path=str(published["status"]),
    )
    return CollectionResult(
        task_count=len(tasks),
        output_path=published["portfolio"],
        summary_path=published["summary"],
        raw_path=published["raw"],
        status_path=published["status"],
    )


def _require_playwright() -> tuple[Any, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CollectorError(
            "PLAYWRIGHT_NOT_INSTALLED",
            "install: python -m pip install -r requirements-playwright.txt",
        ) from exc
    return sync_playwright, PlaywrightError


def install_chromium() -> None:
    _require_playwright()
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            cwd=PROJECT_ROOT,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CollectorError("PLAYWRIGHT_BROWSER_INSTALL_FAILED", type(exc).__name__) from exc


def _captured_request_from_mapping(meta: Mapping[str, Any]) -> CapturedRequest:
    url = str(meta.get("url") or "")
    _validate_marketdetector_url(url)
    symbol = normalize_symbol(meta.get("captured_symbol"))
    from_date = normalize_date(meta.get("captured_from_date"))
    to_date = normalize_date(meta.get("captured_to_date"))
    if not symbol or not from_date or not to_date:
        raise CollectorError("REQUEST_TEMPLATE_IDENTITY_MISSING")
    headers_raw = meta.get("headers")
    headers = (
        {str(key): str(value) for key, value in headers_raw.items()}
        if isinstance(headers_raw, Mapping)
        else {}
    )
    body_kind = str(meta.get("body_kind") or "none").lower()
    if body_kind not in {"none", "json", "urlencoded", "formdata", "text"}:
        raise CollectorError("REQUEST_BODY_UNSUPPORTED", body_kind)
    body_value_raw = meta.get("body_value")
    return CapturedRequest(
        url=url,
        method=str(meta.get("method") or "GET").upper(),
        headers=headers,
        body_kind=body_kind,
        body_value=None if body_value_raw is None else str(body_value_raw),
        transport="XHR" if str(meta.get("transport")).upper() == "XHR" else "FETCH",
        captured_symbol=symbol,
        captured_from_date=from_date,
        captured_to_date=to_date,
    )


class StockbitPlaywrightSession:
    """Persistent-profile, read-only Stockbit Stock Activity browser session."""

    def __init__(self, profile_dir: Path, headless: bool, timeout_ms: int, diagnostic_dir: Path) -> None:
        sync_playwright, playwright_error = _require_playwright()
        self._playwright_error = playwright_error
        self._manager = sync_playwright().start()
        ensure_dir(profile_dir)
        ensure_dir(diagnostic_dir)
        self._diagnostic_dir = diagnostic_dir
        try:
            self._context = self._manager.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=bool(headless),
                accept_downloads=False,
                viewport={"width": 1440, "height": 1000},
            )
        except Exception as exc:
            self._manager.stop()
            detail = str(exc).lower()
            code = "PLAYWRIGHT_BROWSER_NOT_INSTALLED" if "executable" in detail else "PLAYWRIGHT_BROWSER_LAUNCH_FAILED"
            raise CollectorError(code, type(exc).__name__) from exc
        try:
            self._context.add_init_script(script=NETWORK_HOOK_SCRIPT)
        except Exception as exc:
            self._context.close()
            self._manager.stop()
            raise CollectorError("PLAYWRIGHT_HOOK_INSTALL_FAILED", type(exc).__name__) from exc
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(int(timeout_ms))
        self._timeout_ms = int(timeout_ms)
        self._fallback_template: CapturedRequest | None = None
        self._request_template: CapturedRequest | None = None
        self._ready = False
        self._symbol_control: Any = None
        self._date_controls: list[Any] = []
        self._page.on("response", self._capture_response)

    @property
    def page(self) -> Any:
        return self._page

    def _capture_response(self, response: Any) -> None:
        url = str(getattr(response, "url", ""))
        if MARKETDETECTOR_PATH not in url.lower():
            return
        try:
            payload = response.json()
        except Exception:
            return
        if not isinstance(payload, Mapping):
            return
        symbol, from_date, to_date = payload_identity(payload, url)
        if not symbol or not from_date or not to_date:
            return
        try:
            request = response.request
            headers = dict(getattr(request, "headers", {}) or {})
            post_data = getattr(request, "post_data", None)
            content_type = next(
                (str(value).lower() for key, value in headers.items() if str(key).lower() == "content-type"),
                "",
            )
            if post_data is None:
                body_kind = "none"
            elif "application/json" in content_type:
                body_kind = "json"
            elif "application/x-www-form-urlencoded" in content_type:
                body_kind = "urlencoded"
            elif "multipart/form-data" in content_type:
                body_kind = "unsupported"
            else:
                body_kind = "text"
            if body_kind == "unsupported":
                return
            self._fallback_template = _captured_request_from_mapping({
                "url": url,
                "method": getattr(request, "method", "GET"),
                "headers": headers,
                "body_kind": body_kind,
                "body_value": post_data,
                "transport": getattr(request, "resource_type", "FETCH"),
                "captured_symbol": symbol,
                "captured_from_date": from_date,
                "captured_to_date": to_date,
            })
        except Exception:
            return

    @staticmethod
    def _first_visible(locator: Any) -> Any | None:
        try:
            count = min(int(locator.count()), 20)
        except Exception:
            return None
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    def _visible_text(self, pattern: str) -> bool:
        try:
            return bool(self._page.get_by_text(re.compile(pattern, re.IGNORECASE)).first.is_visible())
        except Exception:
            return False

    def _security_guard(self) -> None:
        if self._visible_text(r"captcha|verify you are human|security challenge|unusual activity"):
            raise CollectorError("SECURITY_CHALLENGE")

    def assert_authenticated(self) -> None:
        self._security_guard()
        current_url = str(self._page.url or "").lower()
        if any(marker in current_url for marker in ("/login", "/signin", "/sign-in")):
            raise CollectorError("LOGIN_REQUIRED")
        try:
            self._page.get_by_text(re.compile(r"Broker Summary", re.IGNORECASE)).first.wait_for(
                state="visible", timeout=min(self._timeout_ms, 12_000)
            )
        except Exception as exc:
            if self._visible_text(r"log\s*in|login|sign\s*in|masuk"):
                raise CollectorError("LOGIN_REQUIRED") from exc
            raise CollectorError("BROKER_SUMMARY_UI_NOT_READY") from exc

    def open_target(self, *, require_auth: bool = True) -> None:
        try:
            self._page.goto(TARGET_URL, wait_until="domcontentloaded")
        except Exception as exc:
            raise CollectorError("STOCKBIT_UNAVAILABLE", type(exc).__name__) from exc
        self._security_guard()
        if require_auth:
            self.assert_authenticated()

    def _ensure_stock_activity(self) -> None:
        tab = self._first_visible(
            self._page.get_by_role("tab", name=re.compile(r"Stock Activity", re.IGNORECASE))
        )
        if tab is None:
            tab = self._first_visible(
                self._page.get_by_text(re.compile(r"^Stock Activity$", re.IGNORECASE))
            )
        if tab is not None:
            selected = str(tab.get_attribute("aria-selected") or "").lower() == "true"
            if not selected:
                tab.click()
        elif not self._visible_text(r"Broker Summary"):
            raise CollectorError("STOCK_ACTIVITY_TAB_NOT_FOUND")

    def _choose_filter(self, label: str) -> None:
        control = self._first_visible(
            self._page.get_by_role(
                "button",
                name=re.compile(rf"^{re.escape(label)}$", re.IGNORECASE),
            )
        )
        if control is None:
            control = self._first_visible(
                self._page.get_by_text(
                    re.compile(rf"^{re.escape(label)}$", re.IGNORECASE)
                )
            )
        if control is None:
            raise CollectorError("BROKER_FILTER_NOT_FOUND", label)
        pressed = str(control.get_attribute("aria-pressed") or "").lower()
        selected = str(control.get_attribute("aria-selected") or "").lower()
        state = str(control.get_attribute("data-state") or "").lower()
        if pressed == "true" or selected == "true" or state in {"active", "checked", "on"}:
            return
        control.click()
        option = self._first_visible(
            self._page.get_by_role(
                "option",
                name=re.compile(rf"^{re.escape(label)}$", re.IGNORECASE),
            )
        )
        if option is not None:
            option.click()

    def _configure_filters(self) -> None:
        self._choose_filter("All Investor")
        self._choose_filter("Regular")
        net = self._first_visible(
            self._page.get_by_role("button", name=re.compile(r"^Net$", re.IGNORECASE))
        )
        if net is not None:
            pressed = str(net.get_attribute("aria-pressed") or "").lower()
            state = str(net.get_attribute("data-state") or "").lower()
            if pressed == "false" or state in {"off", "unchecked"}:
                net.click()

    def _select_symbol(self, symbol: str) -> None:
        candidates = [
            self._page.get_by_role(
                "combobox", name=re.compile(r"symbol|stock|emiten", re.IGNORECASE)
            ),
            self._page.locator(
                "input[placeholder*='symbol' i], input[placeholder*='stock' i], input[aria-label*='symbol' i]"
            ),
        ]
        control = next(
            (found for found in (self._first_visible(item) for item in candidates) if found is not None),
            None,
        )
        if control is None:
            raise CollectorError("SYMBOL_SELECTOR_NOT_FOUND")
        self._symbol_control = control
        control.click()
        try:
            control.fill(symbol)
        except Exception:
            search = self._first_visible(
                self._page.locator(
                    "input[placeholder*='search' i], input[role='combobox']"
                )
            )
            if search is None:
                raise CollectorError("SYMBOL_SEARCH_NOT_FOUND")
            search.fill(symbol)
        option_pattern = re.compile(rf"^{re.escape(symbol)}(?:\.JK)?$", re.IGNORECASE)
        option = self._first_visible(self._page.get_by_role("option", name=option_pattern))
        if option is None:
            option = self._first_visible(self._page.get_by_text(option_pattern))
        if option is None:
            raise CollectorError("SYMBOL_OPTION_NOT_FOUND", symbol)
        option.click()

    @staticmethod
    def _fill_date(control: Any, value: str) -> None:
        try:
            control.fill(value)
            return
        except Exception:
            pass
        control.click()
        control.press("Control+A")
        control.type(value)

    def _select_date(self, task: BrokerTask) -> None:
        locator = self._page.locator(
            "input[type='date'], input[placeholder*='date' i], input[placeholder*='tanggal' i], "
            "input[aria-label*='date' i], input[aria-label*='tanggal' i]"
        )
        controls: list[Any] = []
        try:
            for index in range(min(int(locator.count()), 4)):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    controls.append(candidate)
        except Exception:
            controls = []
        if not controls:
            trigger = self._first_visible(
                self._page.get_by_role(
                    "button", name=re.compile(r"date|tanggal", re.IGNORECASE)
                )
            )
            if trigger is not None:
                trigger.click()
                locator = self._page.locator(
                    "input[type='date'], input[placeholder*='date' i], input[placeholder*='tanggal' i]"
                )
                try:
                    controls = [
                        locator.nth(index)
                        for index in range(min(int(locator.count()), 4))
                        if locator.nth(index).is_visible()
                    ]
                except Exception:
                    controls = []
        if not controls:
            raise CollectorError("DATE_SELECTOR_NOT_FOUND")
        self._date_controls = controls[:2]
        if len(self._date_controls) == 1:
            self._fill_date(self._date_controls[0], task.to_date)
        else:
            self._fill_date(self._date_controls[0], task.from_date)
            self._fill_date(self._date_controls[1], task.to_date)
        self._date_controls[-1].press("Enter")

    def _apply(self) -> None:
        apply_button = self._first_visible(
            self._page.get_by_role(
                "button", name=re.compile(r"^(Apply|Terapkan|Submit|Show)$", re.IGNORECASE)
            )
        )
        if apply_button is not None:
            apply_button.click()

    @staticmethod
    def _exact_symbol_value(control: Any) -> str:
        values: list[str] = []
        for getter in (
            lambda: control.input_value(),
            lambda: control.get_attribute("value"),
            lambda: control.text_content(),
        ):
            try:
                values.append(str(getter() or "").strip())
            except Exception:
                continue
        for value in values:
            if re.fullmatch(r"[A-Za-z0-9]{2,12}(?:\.JK)?", value):
                return normalize_symbol(value)
        return ""

    def _verify_visible_state(self, task: BrokerTask) -> None:
        if self._symbol_control is not None:
            visible_symbol = self._exact_symbol_value(self._symbol_control)
            if visible_symbol and visible_symbol != task.symbol:
                raise CollectorError(
                    "SYMBOL_STATE_MISMATCH",
                    f"expected={task.symbol};actual={visible_symbol}",
                )
        visible_dates: list[str] = []
        for control in self._date_controls:
            try:
                value = control.input_value()
            except Exception:
                value = ""
            parsed = normalize_date(value)
            if parsed:
                visible_dates.append(parsed)
        if visible_dates and any(value != task.to_date for value in visible_dates):
            raise CollectorError(
                "DATE_STATE_MISMATCH",
                f"expected={task.to_date};actual={','.join(visible_dates)}",
            )

    def _clear_native_capture(self) -> None:
        try:
            self._page.evaluate(
                "() => { if (window.__sdeBrokerCapture) window.__sdeBrokerCapture.latest = null; }"
            )
        except Exception as exc:
            raise CollectorError("REQUEST_CAPTURE_HOOK_UNAVAILABLE", type(exc).__name__) from exc
        self._fallback_template = None

    def _read_native_template(self) -> CapturedRequest | None:
        try:
            meta = self._page.evaluate("() => window.__sdeBrokerCapture?.latest || null")
        except Exception:
            meta = None
        if isinstance(meta, Mapping):
            try:
                return _captured_request_from_mapping(meta)
            except CollectorError:
                pass
        return self._fallback_template

    def _wait_for_request_template(self) -> CapturedRequest:
        deadline = time.monotonic() + self._timeout_ms / 1000.0
        while time.monotonic() < deadline:
            self._security_guard()
            template = self._read_native_template()
            if template is not None:
                return template
            self._page.wait_for_timeout(100)
        if self._visible_text(r"log\s*in|login|sign\s*in|masuk"):
            raise CollectorError("LOGIN_REQUIRED")
        raise CollectorError("REQUEST_TEMPLATE_NOT_CAPTURED")

    def _bootstrap(self, task: BrokerTask) -> None:
        self.open_target(require_auth=True)
        self._ensure_stock_activity()
        self._configure_filters()
        self._clear_native_capture()
        self._select_symbol(task.symbol)
        self._select_date(task)
        self._apply()
        self._request_template = self._wait_for_request_template()
        self._verify_visible_state(task)
        self._ready = True

    def _replay(self, request: ReplayRequest) -> tuple[Mapping[str, Any], str, bool]:
        payload = {
            "url": request.url,
            "method": request.method,
            "headers": dict(request.headers),
            "body": request.body,
            "body_kind": request.body_kind,
            "preferred_transport": request.preferred_transport,
        }
        try:
            result = self._page.evaluate(
                "async ({ request, timeoutMs }) => { "
                "if (typeof window.__sdeBrokerReplay !== 'function') "
                "throw new Error('REPLAY_HOOK_UNAVAILABLE'); "
                "return await window.__sdeBrokerReplay(request, timeoutMs); }",
                {"request": payload, "timeoutMs": self._timeout_ms},
            )
        except Exception as exc:
            self._security_guard()
            if self._visible_text(r"log\s*in|login|sign\s*in|masuk"):
                raise CollectorError("LOGIN_REQUIRED") from exc
            raise CollectorError("BROKER_REQUEST_FAILED", type(exc).__name__) from exc
        if not isinstance(result, Mapping) or not isinstance(result.get("payload"), Mapping):
            raise CollectorError("BROKER_RESPONSE_INVALID")
        return (
            result["payload"],
            str(result.get("transport") or ""),
            bool(result.get("fallback_used")),
        )

    def collect(self, task: BrokerTask) -> TaskCollection:
        if not self._ready:
            self._bootstrap(task)
        if self._request_template is None:
            raise CollectorError("REQUEST_TEMPLATE_NOT_CAPTURED")
        request = build_replay_request(self._request_template, task)
        payload, _transport, _fallback_used = self._replay(request)
        return records_from_payload(payload, request.url, task)

    def capture_failure(self, task: BrokerTask, reason: str) -> None:
        safe_reason = re.sub(r"[^A-Z0-9_-]+", "_", str(reason).upper())[:60]
        name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{task.symbol}_{task.to_date}_{safe_reason}.png"
        self._page.screenshot(path=str(self._diagnostic_dir / name), full_page=True)

    def close(self) -> None:
        try:
            self._context.close()
        finally:
            self._manager.stop()


def _real_session_factory(
    profile_dir: Path,
    headless: bool,
    timeout_ms: int,
    diagnostic_dir: Path,
) -> BrokerSummarySession:
    return StockbitPlaywrightSession(profile_dir, headless, timeout_ms, diagnostic_dir)


def setup_login(
    *,
    profile_dir: Path = DEFAULT_PROFILE,
    log_dir: Path = DEFAULT_LOG_DIR,
    timeout_ms: int = 30_000,
    installer: Callable[[], None] = install_chromium,
    input_fn: Callable[[str], str] = input,
    session_factory: SessionFactory | None = None,
) -> None:
    _require_playwright()
    installer()
    factory = session_factory or _real_session_factory
    session = factory(profile_dir, False, timeout_ms, log_dir)
    try:
        if not isinstance(session, StockbitPlaywrightSession):
            # Test/session adapters may expose the same explicit setup hooks.
            opener = getattr(session, "open_target", None)
            verifier = getattr(session, "assert_authenticated", None)
        else:
            opener = session.open_target
            verifier = session.assert_authenticated
        if not callable(opener) or not callable(verifier):
            raise CollectorError("PLAYWRIGHT_SETUP_ADAPTER_INVALID")
        opener(require_auth=False)
        print("Browser headed dibuka. Login Stockbit secara manual; Trading PIN tidak diperlukan.")
        print(f"Pastikan halaman dapat diakses: {TARGET_URL}")
        input_fn("Tekan Enter setelah login selesai: ")
        opener(require_auth=False)
        verifier()
    finally:
        session.close()


def _state_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", default=str(DEFAULT_STATE))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optional read-only Stockbit Broker Summary collector")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    _state_args(status)
    status.add_argument("--value", action="store_true", help="Print only ON/OFF for batch integration")

    enable = sub.add_parser("enable", aliases=["on"])
    _state_args(enable)
    disable = sub.add_parser("disable", aliases=["off"])
    _state_args(disable)

    count = sub.add_parser("task-count")
    count.add_argument("--tasks", default=str(DEFAULT_TASKS))

    setup = sub.add_parser("setup", aliases=["login"])
    setup.add_argument("--profile", default=str(DEFAULT_PROFILE))
    setup.add_argument("--logs", default=str(DEFAULT_LOG_DIR))
    setup.add_argument("--timeout-ms", type=int, default=30_000)

    collect = sub.add_parser("collect")
    _state_args(collect)
    collect.add_argument("--tasks", default=str(DEFAULT_TASKS))
    collect.add_argument("--output-dir", default=str(default_downloads()))
    collect.add_argument("--profile", default=str(DEFAULT_PROFILE))
    collect.add_argument("--logs", default=str(DEFAULT_LOG_DIR))
    collect.add_argument("--timeout-ms", type=int, default=30_000)
    collect.add_argument("--headed", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "status":
            value = "ON" if load_state(Path(args.state)).enabled else "OFF"
            print(value if args.value else f"Playwright Auto Collector : {value}")
            return 0
        if args.command in {"enable", "on"}:
            save_state(True, Path(args.state))
            print("Playwright Auto Collector : ON")
            return 0
        if args.command in {"disable", "off"}:
            save_state(False, Path(args.state))
            print("Playwright Auto Collector : OFF")
            return 0
        if args.command == "task-count":
            print(len(read_tasks(Path(args.tasks))))
            return 0
        if args.command in {"setup", "login"}:
            setup_login(
                profile_dir=Path(args.profile),
                log_dir=Path(args.logs),
                timeout_ms=args.timeout_ms,
            )
            print("PLAYWRIGHT_SETUP_OK")
            return 0
        if args.command == "collect":
            result = collect_and_publish(
                Path(args.tasks),
                output_dir=Path(args.output_dir),
                state_path=Path(args.state),
                profile_dir=Path(args.profile),
                log_dir=Path(args.logs),
                headed=args.headed,
                timeout_ms=args.timeout_ms,
            )
            if result.task_count == 0:
                print("NO_TASKS: browser tidak dijalankan.")
                return 0
            print("\n[AUTO DOWNLOAD SELESAI]")
            print(f"Task berhasil : {result.task_count}/{result.task_count}")
            print(f"Portfolio CSV : {result.output_path}")
            print(f"Summary CSV   : {result.summary_path}")
            print(f"Raw CSV       : {result.raw_path}")
            print(f"Status CSV    : {result.status_path}")
            print("Database BELUM diubah.")
            print("NEXT: Pilih [2] IMPORT HASIL ke database.")
            return 0
        raise CollectorError("COMMAND_NOT_SUPPORTED", args.command)
    except CollectorError as exc:
        print("\n[PLAYWRIGHT FAILED]", file=sys.stderr)
        print(f"Reason: {exc.code}", file=sys.stderr)
        if exc.detail:
            print(f"Detail: {exc.detail}", file=sys.stderr)
        print("Database tidak diubah.", file=sys.stderr)
        print("Final CSV baru tidak dibuat.", file=sys.stderr)
        print("Gunakan [6] SETUP / LOGIN PLAYWRIGHT atau [5] PLAYWRIGHT OFF,", file=sys.stderr)
        print("kemudian lanjutkan workflow Tampermonkey manual.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
