// ==UserScript==
// @name         SDE Broker Portfolio Backfill v1
// @namespace    https://stockbit.com/
// @version      1.0.0
// @description  Backfill Broker Summary DAILY untuk portfolio SDE tanpa menyentuh batch Broker Summary Final Watchlist.
// @match        https://stockbit.com/*
// @match        https://*.stockbit.com/*
// @grant        none
// @run-at       document-start
// ==/UserScript==

(function () {
  'use strict';

  // Dedicated namespace: do not share localStorage/IndexedDB/output filenames
  // with Stockbit_Broker_Summary_Auto_Navigator_v3.1.user.js.
  const APP_ID = 'sde-broker-portfolio-backfill-v1';
  const TASKS_KEY = 'sde_broker_portfolio_backfill_v1_tasks';
  const JOB_KEY = 'sde_broker_portfolio_backfill_v1_job';
  const DB_NAME = 'sde_broker_portfolio_backfill_v1_db';
  const DB_VERSION = 1;
  const PAGE_TIMEOUT_MS = 30000;
  const NEXT_DELAY_MS = 1800;
  const EXCLUDED = new Set(['BRENT', 'OIL', 'XAU', 'IHSG']);

  let capturedPageSymbol = '';
  let pageHandled = false;
  let timeoutId = null;

  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

  function loadJson(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback)); }
    catch { return fallback; }
  }

  function saveJson(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
  }

  function getJob() {
    return loadJson(JOB_KEY, {
      running: false,
      index: 0,
      success: 0,
      failed: 0,
      templateUrl: '',
      templateSymbol: '',
    });
  }

  function setJob(patch) {
    const next = { ...getJob(), ...patch };
    saveJson(JOB_KEY, next);
    return next;
  }

  function getTasks() {
    return loadJson(TASKS_KEY, []);
  }

  function currentTask() {
    const job = getJob();
    return getTasks()[job.index] || null;
  }

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('summary')) {
          db.createObjectStore('summary', { keyPath: 'TASK_KEY' });
        }
        if (!db.objectStoreNames.contains('status')) {
          db.createObjectStore('status', { keyPath: 'TASK_KEY' });
        }
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

  function isTargetUrl(url) {
    return String(url || '').includes('/marketdetectors/');
  }

  function safeNumber(value) {
    if (value === null || value === undefined || value === '') return 0;
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function normalizeSymbol(value) {
    const first = String(value ?? '').trim().split(/\r?\n/)[0];
    const match = first.toUpperCase().replace('.JK', '').match(/[A-Z0-9]{2,12}/);
    return match ? match[0] : '';
  }

  function normalizeDate(value) {
    const text = String(value ?? '').trim();
    const match = text.match(/\d{4}-\d{2}-\d{2}/);
    return match ? match[0] : '';
  }

  function getBrokerCode(item) {
    return item?.netbs_broker_code || item?.broker_code || item?.code || '';
  }

  function getBuyNetValue(item) {
    return safeNumber(item?.bvalv ?? item?.net_value ?? item?.bval ?? 0);
  }

  function getSellNetValue(item) {
    const raw = safeNumber(item?.svalv ?? item?.net_value ?? item?.sval ?? 0);
    return raw > 0 ? -raw : raw;
  }

  function extractSymbol(payload, requestUrl = '') {
    return normalizeSymbol(
      payload?.data?.broker_summary?.symbol ||
      String(requestUrl).split('/marketdetectors/')[1]?.split(/[/?#]/)[0] ||
      ''
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
        out.push(cell);
        cell = '';
      } else {
        cell += ch;
      }
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
    if (symbolIdx < 0 || fromIdx < 0 || toIdx < 0) {
      throw new Error('CSV backfill wajib memiliki Symbol, FROM_DATE, TO_DATE.');
    }

    const tasks = [];
    const seen = new Set();
    for (let i = 1; i < lines.length; i++) {
      const cells = parseCsvLine(lines[i]);
      const symbol = normalizeSymbol(cells[symbolIdx]);
      const fromDate = normalizeDate(cells[fromIdx]);
      const toDate = normalizeDate(cells[toIdx]);
      if (!symbol || EXCLUDED.has(symbol) || !fromDate || !toDate) continue;
      if (fromDate !== toDate) {
        throw new Error(`Baris ${i + 1}: backfill harus DAILY (FROM_DATE harus sama dengan TO_DATE).`);
      }
      const taskKey = String(taskIdx >= 0 ? cells[taskIdx] : '').trim() || `${symbol}|${toDate}`;
      if (seen.has(taskKey)) continue;
      seen.add(taskKey);
      tasks.push({ symbol, fromDate, toDate, taskKey });
    }
    return tasks;
  }

  function replaceSymbolInUrl(templateUrl, oldSymbol, newSymbol) {
    if (!templateUrl || !oldSymbol) return '';
    const escaped = oldSymbol.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(?<![A-Z0-9])${escaped}(?![A-Z0-9])`, 'ig');
    const replaced = templateUrl.replace(regex, newSymbol);
    return replaced !== templateUrl ? replaced : templateUrl;
  }

  function rewriteTargetUrl(rawUrl, task) {
    if (!task || !isTargetUrl(rawUrl)) return String(rawUrl || '');
    try {
      const url = new URL(String(rawUrl), location.origin);
      const aliases = {
        from: ['from', 'from_date', 'start', 'start_date', 'date_from'],
        to: ['to', 'to_date', 'end', 'end_date', 'date_to'],
      };
      const keys = Array.from(url.searchParams.keys());
      for (const key of keys) {
        const lower = key.toLowerCase();
        if (aliases.from.includes(lower)) url.searchParams.set(key, task.fromDate);
        if (aliases.to.includes(lower)) url.searchParams.set(key, task.toDate);
      }
      // Stockbit Broker Summary currently exposes data.from/data.to.  Always
      // provide canonical query names as well.  Returned payload is validated
      // before persistence, so an incompatible endpoint fails closed.
      url.searchParams.set('from', task.fromDate);
      url.searchParams.set('to', task.toDate);
      return url.toString();
    } catch {
      return String(rawUrl || '');
    }
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
      TASK_KEY: task.taskKey,
      FROM_DATE: fromDate,
      TO_DATE: toDate,
      EMITEN: symbol,
      TOTAL_BUY: totalBuy,
      TOTAL_SELL: totalSell,
      NET_FLOW: totalBuy - totalSell,
      TOP_BUYER_1: getBrokerCode(buyers[0]),
      TOP_BUYER_2: getBrokerCode(buyers[1]),
      TOP_BUYER_3: getBrokerCode(buyers[2]),
      TOP_SELLER_1: getBrokerCode(sellers[0]),
      TOP_SELLER_2: getBrokerCode(sellers[1]),
      TOP_SELLER_3: getBrokerCode(sellers[2]),
      BUYER_CONCENTRATION: totalBuy ? top3Buy / totalBuy : 0,
      SELLER_CONCENTRATION: totalSell ? top3Sell / totalSell : 0,
      BROKER_ACCDIST: detector?.broker_accdist || '',
      AVG_ACCDIST: detector?.avg?.accdist || '',
      AVG_AMOUNT: safeNumber(detector?.avg?.amount),
      AVG_PERCENT: safeNumber(detector?.avg?.percent),
      TOP3_ACCDIST: detector?.top3?.accdist || '',
      TOP3_AMOUNT: safeNumber(detector?.top3?.amount),
      TOP3_PERCENT: safeNumber(detector?.top3?.percent),
      TOTAL_BUYER_COUNT: safeNumber(detector?.total_buyer),
      TOTAL_SELLER_COUNT: safeNumber(detector?.total_seller),
      TOTAL_VALUE: safeNumber(detector?.value),
      TOTAL_VOLUME: safeNumber(detector?.volume),
      _BUYERS: buyers.length,
      _SELLERS: sellers.length,
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
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  async function exportAll() {
    const summaries = await getAll('summary');
    const statuses = await getAll('status');
    summaries.sort((a, b) => a.TO_DATE.localeCompare(b.TO_DATE) || a.EMITEN.localeCompare(b.EMITEN));
    statuses.sort((a, b) => a.ORDER - b.ORDER);
    const dates = summaries.map(row => row.TO_DATE).filter(Boolean).sort();
    const stamp = dates.length ? dates[dates.length - 1] : new Date().toISOString().slice(0, 10);
    const summaryHeader = [
      'FROM_DATE','TO_DATE','EMITEN','TOTAL_BUY','TOTAL_SELL','NET_FLOW',
      'TOP_BUYER_1','TOP_BUYER_2','TOP_BUYER_3','TOP_SELLER_1','TOP_SELLER_2','TOP_SELLER_3',
      'BUYER_CONCENTRATION','SELLER_CONCENTRATION','BROKER_ACCDIST','AVG_ACCDIST',
      'AVG_AMOUNT','AVG_PERCENT','TOP3_ACCDIST','TOP3_AMOUNT','TOP3_PERCENT',
      'TOTAL_BUYER_COUNT','TOTAL_SELLER_COUNT','TOTAL_VALUE','TOTAL_VOLUME','TASK_KEY'
    ];
    downloadCsv(`BROKER_PORTFOLIO_BACKFILL_SUMMARY_${stamp}.csv`, summaryHeader, summaries);
    await wait(500);
    downloadCsv(
      `BROKER_PORTFOLIO_BACKFILL_STATUS_${stamp}.csv`,
      ['TASK_KEY','SYMBOL','FROM_DATE','TO_DATE','STATUS','MESSAGE','ORDER'],
      statuses
    );
  }

  function setStatus(text, kind = 'normal') {
    const el = document.getElementById(`${APP_ID}-status`);
    if (!el) return;
    el.textContent = text;
    el.style.color = kind === 'error' ? '#ff8a80' : kind === 'ok' ? '#80e27e' : '#fff';
  }

  function refreshPanel() {
    const tasks = getTasks();
    const job = getJob();
    const task = tasks[job.index] || null;
    const count = document.getElementById(`${APP_ID}-count`);
    const progress = document.getElementById(`${APP_ID}-progress`);
    const current = document.getElementById(`${APP_ID}-current`);
    const endpoint = document.getElementById(`${APP_ID}-endpoint`);
    if (count) count.textContent = `${tasks.length} task harian dimuat`;
    if (progress) progress.textContent = `${Math.min(job.index, tasks.length)}/${tasks.length} • success=${job.success || 0} • failed=${job.failed || 0}`;
    if (current) current.textContent = task ? `${task.symbol} • ${task.toDate}` : '-';
    if (endpoint) endpoint.textContent = capturedPageSymbol ? `Payload terlihat: ${capturedPageSymbol}` : 'Buka Broker Summary satu saham terlebih dahulu';
  }

  async function navigateToIndex(index) {
    const tasks = getTasks();
    const job = getJob();
    if (!job.running) return;
    if (index >= tasks.length) {
      setJob({ running: false, index: tasks.length });
      await exportAll();
      setStatus(`Selesai. success=${job.success || 0}, failed=${job.failed || 0}`, job.failed ? 'error' : 'ok');
      refreshPanel();
      return;
    }

    const task = tasks[index];
    const url = replaceSymbolInUrl(job.templateUrl, job.templateSymbol, task.symbol);
    if (!url) {
      setJob({ running: false });
      setStatus('Gagal membentuk URL Broker Summary.', 'error');
      return;
    }
    pageHandled = false;
    clearTimeout(timeoutId);
    if (url === location.href) {
      location.reload();
    } else {
      location.href = url;
    }
  }

  async function markFailedAndContinue(message) {
    if (pageHandled) return;
    pageHandled = true;
    clearTimeout(timeoutId);
    const task = currentTask();
    const job = getJob();
    if (!job.running || !task) return;
    await put('status', {
      TASK_KEY: task.taskKey,
      SYMBOL: task.symbol,
      FROM_DATE: task.fromDate,
      TO_DATE: task.toDate,
      STATUS: 'failed',
      MESSAGE: message,
      ORDER: job.index,
    });
    const next = setJob({ index: job.index + 1, failed: (job.failed || 0) + 1 });
    setStatus(`Gagal ${task.symbol} ${task.toDate}: ${message}`, 'error');
    await wait(NEXT_DELAY_MS);
    navigateToIndex(next.index);
  }

  async function handlePayload(url, payload) {
    const symbol = extractSymbol(payload, url);
    if (symbol) {
      capturedPageSymbol = symbol;
      refreshPanel();
    }

    const job = getJob();
    const task = currentTask();
    if (!job.running || pageHandled || !task || !symbol || symbol !== task.symbol) return;

    const result = makeSummary(payload, url, task);
    // Fail closed: a rewritten request is accepted only when Stockbit itself
    // returns the exact requested DAILY date.  No cumulative or wrong-date row
    // is allowed into the backfill export/database.
    if (result.FROM_DATE !== task.fromDate || result.TO_DATE !== task.toDate) {
      return;
    }

    pageHandled = true;
    clearTimeout(timeoutId);
    try {
      await put('summary', result);
      await put('status', {
        TASK_KEY: task.taskKey,
        SYMBOL: task.symbol,
        FROM_DATE: task.fromDate,
        TO_DATE: task.toDate,
        STATUS: 'success',
        MESSAGE: '',
        ORDER: job.index,
      });
      const next = setJob({ index: job.index + 1, success: (job.success || 0) + 1 });
      setStatus(`Berhasil ${task.symbol} ${task.toDate}.`, 'ok');
      await wait(NEXT_DELAY_MS);
      navigateToIndex(next.index);
    } catch (error) {
      pageHandled = false;
      await markFailedAndContinue(String(error?.message || error));
    }
  }

  function armTimeout() {
    clearTimeout(timeoutId);
    const job = getJob();
    const task = currentTask();
    if (!job.running || !task) return;
    timeoutId = setTimeout(
      () => markFailedAndContinue(`Timeout menunggu DAILY Broker Summary ${task.toDate}`),
      PAGE_TIMEOUT_MS
    );
  }

  function createPanel() {
    if (document.getElementById(APP_ID) || !document.body) return;
    const panel = document.createElement('div');
    panel.id = APP_ID;
    Object.assign(panel.style, {
      position: 'fixed', left: '16px', bottom: '16px', zIndex: '9999998', width: '330px', padding: '14px',
      borderRadius: '12px', background: 'rgba(29,31,38,.97)', color: '#fff', fontFamily: 'Arial,sans-serif',
      fontSize: '12px', boxShadow: '0 8px 30px rgba(0,0,0,.4)', border: '1px solid #665c3c'
    });
    panel.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <strong style="font-size:14px">SDE Portfolio Broker Backfill</strong>
        <button id="${APP_ID}-min">—</button>
      </div>
      <div id="${APP_ID}-body">
        <div style="color:#ffd180;margin-bottom:6px">Mode TERPISAH dari Broker Final Watchlist</div>
        <div id="${APP_ID}-endpoint" style="margin-bottom:8px;color:#b9d7ff">Buka Broker Summary satu saham terlebih dahulu</div>
        <input id="${APP_ID}-file" type="file" accept=".csv,text/csv" style="display:none">
        <button id="${APP_ID}-import" style="width:100%;padding:9px;margin-bottom:7px">Impor CSV Backfill Task</button>
        <div id="${APP_ID}-count">0 task harian dimuat</div>
        <div style="margin-top:5px">Current: <b id="${APP_ID}-current">-</b></div>
        <div id="${APP_ID}-progress" style="margin:7px 0">0/0</div>
        <div style="display:flex;gap:7px">
          <button id="${APP_ID}-start" style="flex:1;padding:9px;background:#8a6b18;color:white;border:0;border-radius:7px">Mulai / Resume</button>
          <button id="${APP_ID}-stop" style="padding:9px;background:#a33b3b;color:white;border:0;border-radius:7px">Stop</button>
        </div>
        <button id="${APP_ID}-export" style="width:100%;padding:8px;margin-top:7px">Export Backfill Sekarang</button>
        <div id="${APP_ID}-status" style="margin-top:9px;min-height:30px">Siap.</div>
      </div>`;
    document.body.appendChild(panel);

    document.getElementById(`${APP_ID}-import`).onclick = () => document.getElementById(`${APP_ID}-file`).click();
    document.getElementById(`${APP_ID}-file`).onchange = async event => {
      try {
        const file = event.target.files?.[0];
        if (!file) return;
        const tasks = parseTasksFromCsv(await file.text());
        if (!tasks.length) throw new Error('Tidak ada task backfill valid di CSV.');
        saveJson(TASKS_KEY, tasks);
        setJob({ running: false, index: 0, success: 0, failed: 0, templateUrl: '', templateSymbol: '' });
        await clearStores();
        refreshPanel();
        setStatus('CSV backfill berhasil dimuat.', 'ok');
      } catch (error) {
        setStatus(String(error?.message || error), 'error');
      }
    };

    document.getElementById(`${APP_ID}-start`).onclick = async () => {
      const tasks = getTasks();
      if (!tasks.length) return setStatus('Impor CSV backfill terlebih dahulu.', 'error');
      if (!capturedPageSymbol) return setStatus('Buka Broker Summary satu saham dan tunggu payload terlihat.', 'error');
      let job = getJob();
      if (!job.running && job.index >= tasks.length) {
        await clearStores();
        job = setJob({ index: 0, success: 0, failed: 0 });
      }
      if (!job.templateUrl || !job.templateSymbol || job.index === 0) {
        await clearStores();
        job = setJob({
          running: true,
          index: 0,
          success: 0,
          failed: 0,
          templateUrl: location.href,
          templateSymbol: capturedPageSymbol,
        });
      } else {
        job = setJob({ running: true });
      }
      setStatus('Backfill dimulai. Setiap payload wajib cocok symbol + tanggal.', 'ok');
      navigateToIndex(job.index);
    };

    document.getElementById(`${APP_ID}-stop`).onclick = () => {
      clearTimeout(timeoutId);
      setJob({ running: false });
      setStatus('Backfill dihentikan. Data yang berhasil tetap tersimpan.');
      refreshPanel();
    };
    document.getElementById(`${APP_ID}-export`).onclick = exportAll;
    document.getElementById(`${APP_ID}-min`).onclick = () => {
      const body = document.getElementById(`${APP_ID}-body`);
      body.style.display = body.style.display === 'none' ? 'block' : 'none';
    };
    refreshPanel();
  }

  // Fetch interception.  Rewrite dates only while a backfill job is running.
  const previousFetch = window.fetch;
  window.fetch = async function (...args) {
    let usedArgs = args;
    let requestUrl = typeof args[0] === 'string' ? args[0] : args[0]?.url;
    try {
      const job = getJob();
      const task = currentTask();
      if (job.running && task && isTargetUrl(requestUrl)) {
        const rewritten = rewriteTargetUrl(requestUrl, task);
        if (typeof args[0] === 'string') {
          usedArgs = [rewritten, args[1]];
        } else if (args[0] instanceof Request) {
          usedArgs = [new Request(rewritten, args[0]), args[1]];
        }
        requestUrl = rewritten;
      }
    } catch (error) {
      console.warn('[SDE Portfolio Backfill] fetch rewrite gagal:', error);
    }

    const response = await previousFetch.apply(this, usedArgs);
    try {
      if (isTargetUrl(requestUrl)) handlePayload(requestUrl, await response.clone().json());
    } catch (error) {
      console.warn('[SDE Portfolio Backfill] fetch capture gagal:', error);
    }
    return response;
  };

  // XHR interception with the same fail-closed date rewrite.
  const previousOpen = XMLHttpRequest.prototype.open;
  const previousSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    let usedUrl = url;
    try {
      const job = getJob();
      const task = currentTask();
      if (job.running && task && isTargetUrl(url)) usedUrl = rewriteTargetUrl(url, task);
    } catch (error) {
      console.warn('[SDE Portfolio Backfill] XHR rewrite gagal:', error);
    }
    this.__sdePortfolioBackfillUrl = usedUrl;
    return previousOpen.call(this, method, usedUrl, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener('load', function () {
      try {
        if (isTargetUrl(this.__sdePortfolioBackfillUrl)) {
          handlePayload(this.__sdePortfolioBackfillUrl, JSON.parse(this.responseText));
        }
      } catch (error) {
        console.warn('[SDE Portfolio Backfill] XHR capture gagal:', error);
      }
    });
    return previousSend.apply(this, args);
  };

  window.addEventListener('DOMContentLoaded', () => {
    createPanel();
    armTimeout();
  });
  setInterval(() => {
    createPanel();
    const job = getJob();
    if (job.running && !timeoutId) armTimeout();
  }, 1500);
})();
