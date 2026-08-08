// ==UserScript==
// @name         SDE Broker Portfolio Backfill v2
// @namespace    https://stockbit.com/
// @version      2.1.0
// @description  Backfill Broker Summary DAILY + nominal top broker untuk portfolio SDE; terpisah dari Broker Final Watchlist.
// @match        https://stockbit.com/*
// @match        https://*.stockbit.com/*
// @grant        none
// @run-at       document-start
// ==/UserScript==

(function () {
  'use strict';

  const APP_ID = 'sde-broker-portfolio-backfill-v2';
  const TASKS_KEY = 'sde_broker_portfolio_backfill_v2_tasks';
  const DB_NAME = 'sde_broker_portfolio_backfill_v2_db';
  const DB_VERSION = 1;
  const DELAY_MS = 1800;
  const EXCLUDED = new Set(['BRENT', 'OIL', 'XAU', 'IHSG']);
  const DATE_FROM_KEYS = new Set(['from', 'from_date', 'start', 'start_date', 'date_from']);
  const DATE_TO_KEYS = new Set(['to', 'to_date', 'end', 'end_date', 'date_to']);

  let templateRequest = null;
  let templateSymbol = '';
  let templateFromDate = '';
  let templateToDate = '';
  let running = false;
  let stopRequested = false;
  let currentIndex = 0;
  let successCount = 0;
  let failedCount = 0;

  const nativeFetch = window.fetch.bind(window);
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

  function loadJson(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback)); }
    catch { return fallback; }
  }
  function saveJson(key, value) { localStorage.setItem(key, JSON.stringify(value)); }
  function getTasks() { return loadJson(TASKS_KEY, []); }

  function normalizeSymbol(value) {
    const first = String(value ?? '').trim().split(/\r?\n/)[0];
    const match = first.toUpperCase().replace('.JK', '').match(/[A-Z0-9]{2,12}/);
    return match ? match[0] : '';
  }
  function normalizeDate(value) {
    const match = String(value ?? '').trim().match(/\d{4}-\d{2}-\d{2}/);
    return match ? match[0] : '';
  }
  function isTargetUrl(url) { return String(url || '').includes('/marketdetectors/'); }
  function safeNumber(value) {
    if (value === null || value === undefined || value === '') return 0;
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }
  function getBrokerCode(item) { return item?.netbs_broker_code || item?.broker_code || item?.code || ''; }
  function getBuyNetValue(item) { return safeNumber(item?.bvalv ?? item?.net_value ?? item?.bval ?? 0); }
  function getSellNetValue(item) {
    const raw = safeNumber(item?.svalv ?? item?.net_value ?? item?.sval ?? 0);
    return raw > 0 ? -raw : raw;
  }
  function extractSymbol(payload, requestUrl = '') {
    return normalizeSymbol(
      payload?.data?.broker_summary?.symbol ||
      String(requestUrl).split('/marketdetectors/')[1]?.split(/[/?#]/)[0] || ''
    );
  }

  function parseCsvLine(line) {
    const out = [];
    let cell = '';
    let quoted = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        if (quoted && line[i + 1] === '"') { cell += '"'; i++; }
        else quoted = !quoted;
      } else if (ch === ',' && !quoted) {
        out.push(cell); cell = '';
      } else cell += ch;
    }
    out.push(cell);
    return out;
  }

  function parseTasksFromCsv(text) {
    const lines = String(text).replace(/^\ufeff/, '').split(/\r?\n/).filter(x => x.trim());
    if (!lines.length) return [];
    const header = parseCsvLine(lines[0]).map(x => x.trim().toUpperCase());
    const symbolIdx = header.findIndex(x => ['SYMBOL', 'EMITEN', 'TICKER', 'CODE'].includes(x));
    const fromIdx = header.indexOf('FROM_DATE');
    const toIdx = header.indexOf('TO_DATE');
    const taskIdx = header.indexOf('TASK_KEY');
    if (symbolIdx < 0 || fromIdx < 0 || toIdx < 0) throw new Error('CSV wajib memiliki Symbol, FROM_DATE, TO_DATE.');
    const tasks = [];
    const seen = new Set();
    for (let i = 1; i < lines.length; i++) {
      const cells = parseCsvLine(lines[i]);
      const symbol = normalizeSymbol(cells[symbolIdx]);
      const fromDate = normalizeDate(cells[fromIdx]);
      const toDate = normalizeDate(cells[toIdx]);
      if (!symbol || EXCLUDED.has(symbol) || !fromDate || !toDate) continue;
      if (fromDate !== toDate) throw new Error(`Baris ${i + 1}: backfill wajib DAILY.`);
      const taskKey = String(taskIdx >= 0 ? cells[taskIdx] : '').trim() || `${symbol}|${toDate}`;
      if (seen.has(taskKey)) continue;
      seen.add(taskKey);
      tasks.push({ symbol, fromDate, toDate, taskKey });
    }
    return tasks;
  }

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('summary')) db.createObjectStore('summary', { keyPath: 'TASK_KEY' });
        if (!db.objectStoreNames.contains('status')) db.createObjectStore('status', { keyPath: 'TASK_KEY' });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  async function clearStores() {
    const db = await openDb();
    await Promise.all(['summary', 'status'].map(name => new Promise((resolve, reject) => {
      const tx = db.transaction(name, 'readwrite');
      tx.objectStore(name).clear();
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    })));
    db.close();
  }
  async function put(store, value) {
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readwrite');
      tx.objectStore(store).put(value);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  }
  async function getAll(store) {
    const db = await openDb();
    const values = await new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readonly');
      const req = tx.objectStore(store).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return values;
  }

  function replaceSymbolInUrl(rawUrl, oldSymbol, newSymbol) {
    if (!rawUrl || !oldSymbol) return String(rawUrl || '');
    const escaped = oldSymbol.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return String(rawUrl).replace(new RegExp(`(?<![A-Z0-9])${escaped}(?![A-Z0-9])`, 'ig'), newSymbol);
  }

  function rewriteUrl(rawUrl, task) {
    let text = replaceSymbolInUrl(rawUrl, templateSymbol, task.symbol);
    try {
      const url = new URL(text, location.origin);
      let fromFound = false;
      let toFound = false;
      for (const key of Array.from(url.searchParams.keys())) {
        const lower = key.toLowerCase();
        if (DATE_FROM_KEYS.has(lower)) { url.searchParams.set(key, task.fromDate); fromFound = true; }
        if (DATE_TO_KEYS.has(lower)) { url.searchParams.set(key, task.toDate); toFound = true; }
      }
      if (!fromFound) url.searchParams.set('from', task.fromDate);
      if (!toFound) url.searchParams.set('to', task.toDate);
      text = url.toString();
    } catch {}
    if (templateFromDate) text = text.split(templateFromDate).join(task.fromDate);
    if (templateToDate) text = text.split(templateToDate).join(task.toDate);
    return text;
  }

  function rewriteJsonDates(value, task, key = '') {
    if (Array.isArray(value)) return value.map(item => rewriteJsonDates(item, task, key));
    if (value && typeof value === 'object') {
      const out = {};
      for (const [childKey, childValue] of Object.entries(value)) out[childKey] = rewriteJsonDates(childValue, task, childKey);
      return out;
    }
    const lower = String(key || '').toLowerCase();
    if (DATE_FROM_KEYS.has(lower)) return task.fromDate;
    if (DATE_TO_KEYS.has(lower)) return task.toDate;
    if (typeof value === 'string') {
      if (templateFromDate && value.includes(templateFromDate)) return value.split(templateFromDate).join(task.fromDate);
      if (templateToDate && value.includes(templateToDate)) return value.split(templateToDate).join(task.toDate);
    }
    return value;
  }

  async function buildTaskRequest(task) {
    if (!templateRequest) throw new Error('Template request Broker Summary belum tertangkap.');
    const source = templateRequest.clone();
    const url = rewriteUrl(source.url, task);
    if (source.method === 'GET' || source.method === 'HEAD') return new Request(url, source);
    let bodyText = '';
    try { bodyText = await source.clone().text(); } catch {}
    let rewrittenBody = bodyText;
    if (bodyText) {
      try {
        rewrittenBody = JSON.stringify(rewriteJsonDates(JSON.parse(bodyText), task));
      } catch {
        try {
          const params = new URLSearchParams(bodyText);
          let changed = false;
          for (const key of Array.from(params.keys())) {
            const lower = key.toLowerCase();
            if (DATE_FROM_KEYS.has(lower)) { params.set(key, task.fromDate); changed = true; }
            if (DATE_TO_KEYS.has(lower)) { params.set(key, task.toDate); changed = true; }
          }
          if (changed) rewrittenBody = params.toString();
          else {
            if (templateFromDate) rewrittenBody = rewrittenBody.split(templateFromDate).join(task.fromDate);
            if (templateToDate) rewrittenBody = rewrittenBody.split(templateToDate).join(task.toDate);
          }
        } catch {}
      }
    }
    return new Request(url, {
      method: source.method,
      headers: source.headers,
      body: rewrittenBody || undefined,
      credentials: source.credentials,
      mode: source.mode,
      cache: source.cache,
      redirect: source.redirect,
      referrer: source.referrer,
      referrerPolicy: source.referrerPolicy,
      integrity: source.integrity,
      keepalive: source.keepalive,
    });
  }

  function makeSummary(payload, requestUrl, task) {
    const data = payload?.data || {};
    const summary = data?.broker_summary || {};
    const detector = data?.bandar_detector || {};
    const buyers = Array.isArray(summary?.brokers_buy) ? summary.brokers_buy : [];
    const sellers = Array.isArray(summary?.brokers_sell) ? summary.brokers_sell : [];
    const symbol = extractSymbol(payload, requestUrl);
    const fromDate = normalizeDate(data?.from || '');
    const toDate = normalizeDate(data?.to || '');
    const totalBuy = buyers.reduce((sum, item) => sum + Math.abs(getBuyNetValue(item)), 0);
    const totalSell = sellers.reduce((sum, item) => sum + Math.abs(getSellNetValue(item)), 0);
    const top3Buy = buyers.slice(0, 3).reduce((sum, item) => sum + Math.abs(getBuyNetValue(item)), 0);
    const top3Sell = sellers.slice(0, 3).reduce((sum, item) => sum + Math.abs(getSellNetValue(item)), 0);
    return {
      TASK_KEY: task.taskKey, FROM_DATE: fromDate, TO_DATE: toDate, EMITEN: symbol,
      TOTAL_BUY: totalBuy, TOTAL_SELL: totalSell, NET_FLOW: totalBuy - totalSell,
      TOP_BUYER_1: getBrokerCode(buyers[0]), TOP_BUYER_1_VALUE: Math.abs(getBuyNetValue(buyers[0])),
      TOP_BUYER_2: getBrokerCode(buyers[1]), TOP_BUYER_2_VALUE: Math.abs(getBuyNetValue(buyers[1])),
      TOP_BUYER_3: getBrokerCode(buyers[2]), TOP_BUYER_3_VALUE: Math.abs(getBuyNetValue(buyers[2])),
      TOP_SELLER_1: getBrokerCode(sellers[0]), TOP_SELLER_1_VALUE: getSellNetValue(sellers[0]),
      TOP_SELLER_2: getBrokerCode(sellers[1]), TOP_SELLER_2_VALUE: getSellNetValue(sellers[1]),
      TOP_SELLER_3: getBrokerCode(sellers[2]), TOP_SELLER_3_VALUE: getSellNetValue(sellers[2]),
      BUYER_CONCENTRATION: totalBuy ? top3Buy / totalBuy : 0,
      SELLER_CONCENTRATION: totalSell ? top3Sell / totalSell : 0,
      BROKER_ACCDIST: detector?.broker_accdist || '', AVG_ACCDIST: detector?.avg?.accdist || '',
      AVG_AMOUNT: safeNumber(detector?.avg?.amount), AVG_PERCENT: safeNumber(detector?.avg?.percent),
      TOP3_ACCDIST: detector?.top3?.accdist || '', TOP3_AMOUNT: safeNumber(detector?.top3?.amount),
      TOP3_PERCENT: safeNumber(detector?.top3?.percent), TOTAL_BUYER_COUNT: safeNumber(detector?.total_buyer),
      TOTAL_SELLER_COUNT: safeNumber(detector?.total_seller), TOTAL_VALUE: safeNumber(detector?.value),
      TOTAL_VOLUME: safeNumber(detector?.volume)
    };
  }

  function csvEscape(value) {
    const text = value === null || value === undefined ? '' : String(value);
    return '"' + text.replace(/"/g, '""') + '"';
  }
  function downloadCsv(filename, header, records) {
    const rows = [header, ...records.map(record => header.map(key => record[key] ?? ''))];
    const csv = '\ufeff' + rows.map(row => row.map(csvEscape).join(',')).join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = filename; document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  async function exportAll() {
    const summaries = await getAll('summary');
    const statuses = await getAll('status');
    summaries.sort((a, b) => a.TO_DATE.localeCompare(b.TO_DATE) || a.EMITEN.localeCompare(b.EMITEN));
    statuses.sort((a, b) => a.ORDER - b.ORDER);
    const dates = summaries.map(row => row.TO_DATE).filter(Boolean).sort();
    const stamp = dates.length ? dates[dates.length - 1] : new Date().toISOString().slice(0, 10);
    downloadCsv(`BROKER_PORTFOLIO_BACKFILL_SUMMARY_${stamp}.csv`, [
      'FROM_DATE','TO_DATE','EMITEN','TOTAL_BUY','TOTAL_SELL','NET_FLOW',
      'TOP_BUYER_1','TOP_BUYER_1_VALUE','TOP_BUYER_2','TOP_BUYER_2_VALUE','TOP_BUYER_3','TOP_BUYER_3_VALUE',
      'TOP_SELLER_1','TOP_SELLER_1_VALUE','TOP_SELLER_2','TOP_SELLER_2_VALUE','TOP_SELLER_3','TOP_SELLER_3_VALUE',
      'BUYER_CONCENTRATION','SELLER_CONCENTRATION','BROKER_ACCDIST','AVG_ACCDIST','AVG_AMOUNT','AVG_PERCENT',
      'TOP3_ACCDIST','TOP3_AMOUNT','TOP3_PERCENT','TOTAL_BUYER_COUNT','TOTAL_SELLER_COUNT','TOTAL_VALUE','TOTAL_VOLUME','TASK_KEY'
    ], summaries);
    await wait(400);
    downloadCsv(`BROKER_PORTFOLIO_BACKFILL_STATUS_${stamp}.csv`,
      ['TASK_KEY','SYMBOL','FROM_DATE','TO_DATE','STATUS','MESSAGE','ORDER'], statuses);
  }

  function setStatus(text, kind = 'normal') {
    const el = document.getElementById(`${APP_ID}-status`);
    if (!el) return;
    el.textContent = text;
    el.style.color = kind === 'error' ? '#ff8a80' : kind === 'ok' ? '#80e27e' : kind === 'warn' ? '#ffd180' : '#fff';
  }
  function refreshPanel() {
    const tasks = getTasks();
    const task = tasks[currentIndex] || null;
    const count = document.getElementById(`${APP_ID}-count`);
    const progress = document.getElementById(`${APP_ID}-progress`);
    const current = document.getElementById(`${APP_ID}-current`);
    const endpoint = document.getElementById(`${APP_ID}-endpoint`);
    const state = document.getElementById(`${APP_ID}-state`);
    if (count) count.textContent = `${tasks.length} task harian dimuat`;
    if (progress) progress.textContent = `${Math.min(currentIndex, tasks.length)}/${tasks.length} • success=${successCount} • failed=${failedCount}`;
    if (current) current.textContent = task ? `${task.symbol} • ${task.toDate}` : '-';
    if (endpoint) endpoint.textContent = templateRequest ? `Template API: ${templateSymbol} • ${templateFromDate || '?'} → ${templateToDate || '?'}` : 'Buka Broker Summary satu saham sampai payload terlihat';
    if (state) state.textContent = running ? 'RUNNING' : 'STOPPED';
  }

  async function runBatch() {
    if (running) return;
    const tasks = getTasks();
    if (!tasks.length) return setStatus('Impor CSV Backfill Task terlebih dahulu.', 'error');
    if (!templateRequest) return setStatus('Template API belum tertangkap. Buka Broker Summary satu saham dulu.', 'error');
    running = true; stopRequested = false; currentIndex = 0; successCount = 0; failedCount = 0;
    await clearStores();
    refreshPanel();

    for (let i = 0; i < tasks.length; i++) {
      if (stopRequested) break;
      currentIndex = i;
      const task = tasks[i];
      refreshPanel();
      setStatus(`Request ${task.symbol} ${task.toDate}...`);
      try {
        const request = await buildTaskRequest(task);
        const response = await nativeFetch(request);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        const result = makeSummary(payload, request.url, task);
        if (result.EMITEN !== task.symbol) throw new Error(`SYMBOL_MISMATCH returned=${result.EMITEN || '?'} expected=${task.symbol}`);
        if (result.FROM_DATE !== task.fromDate || result.TO_DATE !== task.toDate) {
          throw new Error(`DATE_MISMATCH returned=${result.FROM_DATE || '?'}→${result.TO_DATE || '?'} expected=${task.fromDate}→${task.toDate}`);
        }
        await put('summary', result);
        await put('status', { TASK_KEY: task.taskKey, SYMBOL: task.symbol, FROM_DATE: task.fromDate, TO_DATE: task.toDate,
          STATUS: 'success', MESSAGE: '', ORDER: i });
        successCount++;
        setStatus(`OK ${task.symbol} ${task.toDate}`, 'ok');
      } catch (error) {
        failedCount++;
        const message = String(error?.message || error);
        await put('status', { TASK_KEY: task.taskKey, SYMBOL: task.symbol, FROM_DATE: task.fromDate, TO_DATE: task.toDate,
          STATUS: 'failed', MESSAGE: message, ORDER: i });
        setStatus(`GAGAL ${task.symbol} ${task.toDate}: ${message}`, 'error');
      }
      currentIndex = i + 1;
      refreshPanel();
      if (i < tasks.length - 1 && !stopRequested) await wait(DELAY_MS);
    }

    running = false;
    refreshPanel();
    if (stopRequested) return setStatus(`Dihentikan. success=${successCount}, failed=${failedCount}`, 'warn');
    await exportAll();
    setStatus(`Selesai. success=${successCount}, failed=${failedCount}. CSV diexport.`, failedCount ? 'warn' : 'ok');
  }

  function createPanel() {
    if (document.getElementById(APP_ID) || !document.body) return;
    const panel = document.createElement('div');
    panel.id = APP_ID;
    Object.assign(panel.style, {
      position: 'fixed', left: '16px', bottom: '16px', zIndex: '9999998', width: '350px', padding: '14px',
      borderRadius: '12px', background: 'rgba(29,31,38,.97)', color: '#fff', fontFamily: 'Arial,sans-serif',
      fontSize: '12px', boxShadow: '0 8px 30px rgba(0,0,0,.4)', border: '1px solid #665c3c'
    });
    panel.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <strong style="font-size:14px">SDE Portfolio Broker Backfill v2.1</strong><button id="${APP_ID}-min">—</button>
      </div>
      <div id="${APP_ID}-body">
        <div style="color:#ffd180;margin-bottom:6px">Mode TERPISAH dari Broker Final Watchlist</div>
        <div>Status: <b id="${APP_ID}-state">STOPPED</b></div>
        <div id="${APP_ID}-endpoint" style="margin:7px 0;color:#b9d7ff">Buka Broker Summary satu saham sampai payload terlihat</div>
        <input id="${APP_ID}-file" type="file" accept=".csv,text/csv" style="display:none">
        <button id="${APP_ID}-import" style="width:100%;padding:9px;margin-bottom:7px">Impor CSV Backfill Task</button>
        <div id="${APP_ID}-count">0 task harian dimuat</div>
        <div style="margin-top:5px">Current: <b id="${APP_ID}-current">-</b></div>
        <div id="${APP_ID}-progress" style="margin:7px 0">0/0</div>
        <div style="display:flex;gap:7px">
          <button id="${APP_ID}-start" style="flex:1;padding:9px;background:#8a6b18;color:white;border:0;border-radius:7px">Mulai Backfill</button>
          <button id="${APP_ID}-stop" style="padding:9px;background:#a33b3b;color:white;border:0;border-radius:7px">Stop</button>
        </div>
        <button id="${APP_ID}-export" style="width:100%;padding:8px;margin-top:7px">Export Backfill Sekarang</button>
        <div id="${APP_ID}-status" style="margin-top:9px;min-height:38px">Siap.</div>
      </div>`;
    document.body.appendChild(panel);

    document.getElementById(`${APP_ID}-import`).onclick = () => document.getElementById(`${APP_ID}-file`).click();
    document.getElementById(`${APP_ID}-file`).onchange = async event => {
      try {
        const file = event.target.files?.[0]; if (!file) return;
        const tasks = parseTasksFromCsv(await file.text());
        if (!tasks.length) throw new Error('Tidak ada task valid.');
        saveJson(TASKS_KEY, tasks); currentIndex = 0; successCount = 0; failedCount = 0;
        await clearStores(); refreshPanel(); setStatus('CSV task berhasil dimuat.', 'ok');
      } catch (error) { setStatus(String(error?.message || error), 'error'); }
    };
    document.getElementById(`${APP_ID}-start`).onclick = runBatch;
    document.getElementById(`${APP_ID}-stop`).onclick = () => { stopRequested = true; setStatus('Stop diminta; berhenti setelah request aktif selesai.', 'warn'); };
    document.getElementById(`${APP_ID}-export`).onclick = exportAll;
    document.getElementById(`${APP_ID}-min`).onclick = () => {
      const body = document.getElementById(`${APP_ID}-body`); body.style.display = body.style.display === 'none' ? 'block' : 'none';
    };
    refreshPanel();
  }

  // Capture the exact request Stockbit itself creates. v2 replays this request
  // directly instead of reloading the page for every date.
  window.fetch = async function (...args) {
    const inputUrl = typeof args[0] === 'string' ? args[0] : args[0]?.url;
    let captureCandidate = null;
    try {
      if (isTargetUrl(inputUrl)) captureCandidate = args[0] instanceof Request ? args[0].clone() : new Request(args[0], args[1]);
    } catch {}
    const response = await nativeFetch(...args);
    if (captureCandidate && isTargetUrl(inputUrl)) {
      try {
        const payload = await response.clone().json();
        const symbol = extractSymbol(payload, inputUrl);
        if (symbol) {
          templateRequest = captureCandidate;
          templateSymbol = symbol;
          templateFromDate = normalizeDate(payload?.data?.from || '');
          templateToDate = normalizeDate(payload?.data?.to || '');
          refreshPanel();
          setStatus(`Template API tertangkap: ${symbol} ${templateFromDate || '?'}→${templateToDate || '?'}`, 'ok');
        }
      } catch {}
    }
    return response;
  };

  window.addEventListener('DOMContentLoaded', createPanel);
  setInterval(createPanel, 1500);
})();