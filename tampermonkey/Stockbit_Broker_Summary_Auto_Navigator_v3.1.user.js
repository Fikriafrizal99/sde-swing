// ==UserScript==
// @name         Stockbit Broker Summary Auto Navigator v3.1.0
// @namespace    https://stockbit.com/
// @version      3.1.1
// @description  Membuka halaman Broker Summary tiap emiten, menangkap payload yang dibuat Stockbit sendiri, lalu mengekspor hasil gabungan.
// @match        https://stockbit.com/*
// @match        https://*.stockbit.com/*
// @grant        none
// @run-at       document-start
// ==/UserScript==

(function () {
  'use strict';

  const APP_ID = 'sde-broker-navigator-v3';
  const SYMBOLS_KEY = 'sde_broker_v3_symbols';
  const JOB_KEY = 'sde_broker_v3_job';
  const DB_NAME = 'sde_broker_v3_db';
  const DB_VERSION = 1;
  const PAGE_TIMEOUT_MS = 25000;
  const NEXT_DELAY_MS = 2200;
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
    return loadJson(JOB_KEY, { running: false, index: 0, success: 0, failed: 0, templateUrl: '', templateSymbol: '' });
  }

  function setJob(patch) {
    const next = { ...getJob(), ...patch };
    saveJson(JOB_KEY, next);
    return next;
  }

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('summary')) db.createObjectStore('summary', { keyPath: 'symbol' });
        if (!db.objectStoreNames.contains('raw')) db.createObjectStore('raw', { keyPath: 'id', autoIncrement: true });
        if (!db.objectStoreNames.contains('status')) db.createObjectStore('status', { keyPath: 'symbol' });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function clearStores() {
    const db = await openDb();
    await Promise.all(['summary', 'raw', 'status'].map(name => new Promise((resolve, reject) => {
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

  async function addMany(store, values) {
    if (!values.length) return;
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readwrite');
      const os = tx.objectStore(store);
      values.forEach(value => os.add(value));
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
    const num = Number(value);
    return Number.isFinite(num) ? num : 0;
  }

  function getBrokerCode(item) {
    return item?.netbs_broker_code || item?.broker_code || item?.code || '';
  }

  function getBrokerType(item) {
    return item?.type || item?.investor_type || '';
  }

  function getBuyNetValue(item) {
    return safeNumber(item?.bvalv ?? item?.net_value ?? item?.bval ?? 0);
  }

  function getSellNetValue(item) {
    const raw = safeNumber(item?.svalv ?? item?.net_value ?? item?.sval ?? 0);
    return raw > 0 ? -raw : raw;
  }

  function getBuyLot(item) {
    return safeNumber(item?.blotv ?? item?.blot ?? 0);
  }

  function getSellLot(item) {
    const raw = safeNumber(item?.slotv ?? item?.slot ?? 0);
    return raw > 0 ? -raw : raw;
  }

  function deriveAverage(value, lot) {
    const grossValue = Math.abs(safeNumber(value));
    const grossLot = Math.abs(safeNumber(lot));
    return grossValue > 0 && grossLot > 0 ? grossValue / (grossLot * 100) : 0;
  }

  function getBuyAverage(item) {
    const direct = safeNumber(
      item?.netbs_buy_avg_price ?? item?.buy_avg_price ?? item?.buy_average ??
      item?.bavg ?? item?.avg_buy ?? item?.avg_price ?? 0
    );
    if (direct > 0) return direct;
    return deriveAverage(item?.bval ?? item?.bvalv, item?.blot ?? item?.blotv);
  }

  function getSellAverage(item) {
    const direct = safeNumber(
      item?.netbs_sell_avg_price ?? item?.sell_avg_price ?? item?.sell_average ??
      item?.savg ?? item?.avg_sell ?? item?.avg_price ?? 0
    );
    if (direct > 0) return direct;
    return deriveAverage(item?.sval ?? item?.svalv, item?.slot ?? item?.slotv);
  }

  function extractSymbol(payload, requestUrl = '') {
    return String(
      payload?.data?.broker_summary?.symbol ||
      String(requestUrl).split('/marketdetectors/')[1]?.split(/[/?#]/)[0] ||
      ''
    ).toUpperCase();
  }

  function normalizeSymbol(value) {
    const firstLine = String(value ?? '').trim().split(/\r?\n/)[0];
    const match = firstLine.toUpperCase().match(/[A-Z0-9]{2,12}/);
    return match ? match[0] : '';
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

  function parseSymbolsFromCsv(text) {
    const lines = String(text).replace(/^\ufeff/, '').split(/\r?\n/).filter(x => x.trim());
    if (!lines.length) return [];
    const header = parseCsvLine(lines[0]).map(x => x.trim().toUpperCase());
    let idx = header.findIndex(x => ['SYMBOL', 'EMITEN', 'TICKER', 'CODE'].includes(x));
    const hasHeader = idx >= 0;
    if (!hasHeader) idx = 0;
    const result = [];
    const seen = new Set();
    for (let i = hasHeader ? 1 : 0; i < lines.length; i++) {
      const symbol = normalizeSymbol(parseCsvLine(lines[i])[idx]);
      if (!symbol || EXCLUDED.has(symbol) || seen.has(symbol)) continue;
      seen.add(symbol);
      result.push(symbol);
    }
    return result;
  }

  function replaceSymbolInUrl(templateUrl, oldSymbol, newSymbol) {
    if (!templateUrl || !oldSymbol) return '';
    const escaped = oldSymbol.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(?<![A-Z0-9])${escaped}(?![A-Z0-9])`, 'ig');
    const replaced = templateUrl.replace(regex, newSymbol);
    return replaced !== templateUrl ? replaced : '';
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

  function makeRecords(payload, requestUrl) {
    const data = payload?.data || {};
    const summary = data?.broker_summary || {};
    const detector = data?.bandar_detector || {};
    const buyers = Array.isArray(summary?.brokers_buy) ? summary.brokers_buy : [];
    const sellers = Array.isArray(summary?.brokers_sell) ? summary.brokers_sell : [];
    const symbol = extractSymbol(payload, requestUrl);
    const fromDate = data?.from || '';
    const toDate = data?.to || '';
    const totalBuy = buyers.reduce((sum, item) => sum + Math.abs(getBuyNetValue(item)), 0);
    const totalSell = sellers.reduce((sum, item) => sum + Math.abs(getSellNetValue(item)), 0);
    const top3Buy = buyers.slice(0, 3).reduce((sum, item) => sum + Math.abs(getBuyNetValue(item)), 0);
    const top3Sell = sellers.slice(0, 3).reduce((sum, item) => sum + Math.abs(getSellNetValue(item)), 0);

    const summaryRecord = {
      FROM_DATE: fromDate, TO_DATE: toDate, EMITEN: symbol,
      TOTAL_BUY: totalBuy, TOTAL_SELL: totalSell, NET_FLOW: totalBuy - totalSell,
      TOP_BUYER_1: getBrokerCode(buyers[0]), TOP_BUYER_2: getBrokerCode(buyers[1]), TOP_BUYER_3: getBrokerCode(buyers[2]),
      TOP_SELLER_1: getBrokerCode(sellers[0]), TOP_SELLER_2: getBrokerCode(sellers[1]), TOP_SELLER_3: getBrokerCode(sellers[2]),
      BUYER_CONCENTRATION: totalBuy ? top3Buy / totalBuy : 0,
      SELLER_CONCENTRATION: totalSell ? top3Sell / totalSell : 0,
      BROKER_ACCDIST: detector?.broker_accdist || '', AVG_ACCDIST: detector?.avg?.accdist || '',
      AVG_AMOUNT: safeNumber(detector?.avg?.amount), AVG_PERCENT: safeNumber(detector?.avg?.percent),
      TOP3_ACCDIST: detector?.top3?.accdist || '', TOP3_AMOUNT: safeNumber(detector?.top3?.amount),
      TOP3_PERCENT: safeNumber(detector?.top3?.percent), TOTAL_BUYER_COUNT: safeNumber(detector?.total_buyer),
      TOTAL_SELLER_COUNT: safeNumber(detector?.total_seller), TOTAL_VALUE: safeNumber(detector?.value),
      TOTAL_VOLUME: safeNumber(detector?.volume), symbol
    };

    const common = {
      SYMBOL: symbol, FROM_DATE: fromDate, TO_DATE: toDate,
      BROKER_ACCDIST: detector?.broker_accdist || '', AVG_ACCDIST: detector?.avg?.accdist || '',
      AVG_AMOUNT: safeNumber(detector?.avg?.amount), AVG_PERCENT: safeNumber(detector?.avg?.percent),
      TOP3_ACCDIST: detector?.top3?.accdist || '', TOP3_AMOUNT: safeNumber(detector?.top3?.amount),
      TOP3_PERCENT: safeNumber(detector?.top3?.percent), TOTAL_BUYER: safeNumber(detector?.total_buyer),
      TOTAL_SELLER: safeNumber(detector?.total_seller), TOTAL_VALUE: safeNumber(detector?.value),
      TOTAL_VOLUME: safeNumber(detector?.volume)
    };

    const raw = [];
    buyers.forEach((item, index) => raw.push({ ...common, SIDE: 'BUY', RANK: index + 1,
      BROKER_CODE: getBrokerCode(item), BROKER_TYPE: getBrokerType(item), NET_VALUE: getBuyNetValue(item),
      NET_LOT: getBuyLot(item), GROSS_VALUE: safeNumber(item?.bval), GROSS_LOT: safeNumber(item?.blot),
      FREQUENCY: safeNumber(item?.freq), AVG_PRICE: getBuyAverage(item) }));
    sellers.forEach((item, index) => raw.push({ ...common, SIDE: 'SELL', RANK: index + 1,
      BROKER_CODE: getBrokerCode(item), BROKER_TYPE: getBrokerType(item), NET_VALUE: getSellNetValue(item),
      NET_LOT: getSellLot(item), GROSS_VALUE: safeNumber(item?.sval), GROSS_LOT: safeNumber(item?.slot),
      FREQUENCY: safeNumber(item?.freq), AVG_PRICE: getSellAverage(item) }));

    return { symbol, fromDate, toDate, buyers: buyers.length, sellers: sellers.length, summaryRecord, raw };
  }

  async function exportAll() {
    const summaries = await getAll('summary');
    const raw = await getAll('raw');
    const statuses = await getAll('status');
    summaries.sort((a, b) => a.EMITEN.localeCompare(b.EMITEN));
    raw.sort((a, b) => a.SYMBOL.localeCompare(b.SYMBOL) || a.SIDE.localeCompare(b.SIDE) || a.RANK - b.RANK);
    statuses.sort((a, b) => a.ORDER - b.ORDER);
    const brokerDates = summaries.map(x => String(x.TO_DATE || '')).filter(Boolean).sort();
    const stamp = brokerDates.length ? brokerDates[brokerDates.length - 1] : new Date().toISOString().slice(0, 10);
    downloadCsv(`BROKER_SUMMARY_COMBINED_${stamp}.csv`, [
      'FROM_DATE','TO_DATE','EMITEN','TOTAL_BUY','TOTAL_SELL','NET_FLOW','TOP_BUYER_1','TOP_BUYER_2','TOP_BUYER_3',
      'TOP_SELLER_1','TOP_SELLER_2','TOP_SELLER_3','BUYER_CONCENTRATION','SELLER_CONCENTRATION','BROKER_ACCDIST',
      'AVG_ACCDIST','AVG_AMOUNT','AVG_PERCENT','TOP3_ACCDIST','TOP3_AMOUNT','TOP3_PERCENT','TOTAL_BUYER_COUNT',
      'TOTAL_SELLER_COUNT','TOTAL_VALUE','TOTAL_VOLUME'
    ], summaries);
    await wait(500);
    downloadCsv(`BROKER_RAW_COMBINED_${stamp}.csv`, [
      'SYMBOL','FROM_DATE','TO_DATE','SIDE','RANK','BROKER_CODE','BROKER_TYPE','NET_VALUE','NET_LOT','GROSS_VALUE',
      'GROSS_LOT','FREQUENCY','AVG_PRICE','BROKER_ACCDIST','AVG_ACCDIST','AVG_AMOUNT','AVG_PERCENT','TOP3_ACCDIST',
      'TOP3_AMOUNT','TOP3_PERCENT','TOTAL_BUYER','TOTAL_SELLER','TOTAL_VALUE','TOTAL_VOLUME'
    ], raw);
    await wait(500);
    downloadCsv(`BROKER_STATUS_${stamp}.csv`, ['SYMBOL','STATUS','BUYERS','SELLERS','FROM_DATE','TO_DATE','MESSAGE'], statuses);
  }

  function setStatus(text, kind = 'normal') {
    const el = document.getElementById(`${APP_ID}-status`);
    if (!el) return;
    el.textContent = text;
    el.style.color = kind === 'error' ? '#ff8a80' : kind === 'ok' ? '#80e27e' : '#fff';
  }

  function refreshPanel() {
    const symbols = loadJson(SYMBOLS_KEY, []);
    const job = getJob();
    const count = document.getElementById(`${APP_ID}-count`);
    const progress = document.getElementById(`${APP_ID}-progress`);
    const endpoint = document.getElementById(`${APP_ID}-endpoint`);
    if (count) count.textContent = `${symbols.length} simbol dimuat`;
    if (progress) progress.textContent = `${Math.min(job.index, symbols.length)}/${symbols.length} • success=${job.success || 0} • failed=${job.failed || 0}`;
    if (endpoint) endpoint.textContent = capturedPageSymbol ? `Payload terlihat: ${capturedPageSymbol}` : 'Buka Broker Summary satu saham terlebih dahulu';
  }

  async function navigateToIndex(index) {
    const symbols = loadJson(SYMBOLS_KEY, []);
    const job = getJob();
    if (!job.running) return;
    if (index >= symbols.length) {
      setJob({ running: false, index: symbols.length });
      await exportAll();
      setStatus(`Selesai. success=${job.success || 0}, failed=${job.failed || 0}`, job.failed ? 'error' : 'ok');
      refreshPanel();
      return;
    }
    const symbol = symbols[index];
    const url = replaceSymbolInUrl(job.templateUrl, job.templateSymbol, symbol);
    if (!url) {
      setJob({ running: false });
      setStatus('Gagal membentuk URL. Pastikan URL halaman saat Start mengandung kode saham.', 'error');
      return;
    }
    location.href = url;
  }

  async function markFailedAndContinue(message) {
    if (pageHandled) return;
    pageHandled = true;
    clearTimeout(timeoutId);
    const symbols = loadJson(SYMBOLS_KEY, []);
    const job = getJob();
    if (!job.running) return;
    const expected = symbols[job.index] || '';
    await put('status', { symbol: expected, SYMBOL: expected, STATUS: 'failed', BUYERS: 0, SELLERS: 0,
      FROM_DATE: '', TO_DATE: '', MESSAGE: message, ORDER: job.index });
    const next = setJob({ index: job.index + 1, failed: (job.failed || 0) + 1 });
    setStatus(`Gagal ${expected}: ${message}`, 'error');
    await wait(NEXT_DELAY_MS);
    navigateToIndex(next.index);
  }

  async function handlePayload(url, payload) {
    const symbol = extractSymbol(payload, url);
    if (!symbol) return;
    capturedPageSymbol = symbol;
    refreshPanel();

    const job = getJob();
    const symbols = loadJson(SYMBOLS_KEY, []);
    if (!job.running || pageHandled) return;
    const expected = symbols[job.index];
    if (!expected || symbol !== expected) return;

    pageHandled = true;
    clearTimeout(timeoutId);
    try {
      const result = makeRecords(payload, url);
      await put('summary', result.summaryRecord);
      await addMany('raw', result.raw);
      await put('status', { symbol, SYMBOL: symbol, STATUS: 'success', BUYERS: result.buyers, SELLERS: result.sellers,
        FROM_DATE: result.fromDate, TO_DATE: result.toDate, MESSAGE: '', ORDER: job.index });
      const next = setJob({ index: job.index + 1, success: (job.success || 0) + 1 });
      setStatus(`Berhasil ${symbol}. Lanjut...`, 'ok');
      await wait(NEXT_DELAY_MS);
      navigateToIndex(next.index);
    } catch (error) {
      pageHandled = false;
      await markFailedAndContinue(String(error?.message || error));
    }
  }

  function armTimeout() {
    const job = getJob();
    const symbols = loadJson(SYMBOLS_KEY, []);
    if (!job.running || !symbols[job.index]) return;
    timeoutId = setTimeout(() => markFailedAndContinue(`Timeout ${PAGE_TIMEOUT_MS / 1000} detik menunggu Broker Summary`), PAGE_TIMEOUT_MS);
  }

  function createPanel() {
    if (document.getElementById(APP_ID) || !document.body) return;
    const panel = document.createElement('div');
    panel.id = APP_ID;
    Object.assign(panel.style, {
      position: 'fixed', right: '16px', bottom: '16px', zIndex: '9999999', width: '320px', padding: '14px',
      borderRadius: '12px', background: 'rgba(20,25,30,.97)', color: '#fff', fontFamily: 'Arial,sans-serif',
      fontSize: '12px', boxShadow: '0 8px 30px rgba(0,0,0,.4)', border: '1px solid #3b4650'
    });
    panel.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <strong style="font-size:14px">SDE Broker Navigator v3.1</strong>
        <button id="${APP_ID}-min">—</button>
      </div>
      <div id="${APP_ID}-body">
        <div id="${APP_ID}-endpoint" style="margin-bottom:8px;color:#ffd180">Buka Broker Summary satu saham terlebih dahulu</div>
        <input id="${APP_ID}-file" type="file" accept=".csv,text/csv" style="display:none">
        <button id="${APP_ID}-import" style="width:100%;padding:9px;margin-bottom:7px">Impor CSV Symbol</button>
        <div id="${APP_ID}-count">0 simbol dimuat</div>
        <div id="${APP_ID}-progress" style="margin:7px 0">0/0</div>
        <div style="display:flex;gap:7px">
          <button id="${APP_ID}-start" style="flex:1;padding:9px;background:#1f8f5f;color:white;border:0;border-radius:7px">Mulai / Resume</button>
          <button id="${APP_ID}-stop" style="padding:9px;background:#a33b3b;color:white;border:0;border-radius:7px">Stop</button>
        </div>
        <button id="${APP_ID}-export" style="width:100%;padding:8px;margin-top:7px">Export Sekarang</button>
        <div id="${APP_ID}-status" style="margin-top:9px;min-height:30px">Siap.</div>
      </div>`;
    document.body.appendChild(panel);

    document.getElementById(`${APP_ID}-import`).onclick = () => document.getElementById(`${APP_ID}-file`).click();
    document.getElementById(`${APP_ID}-file`).onchange = async event => {
      const file = event.target.files?.[0];
      if (!file) return;
      const symbols = parseSymbolsFromCsv(await file.text());
      saveJson(SYMBOLS_KEY, symbols);
      setJob({ running: false, index: 0, success: 0, failed: 0 });
      refreshPanel();
      setStatus(symbols.length ? 'CSV berhasil dimuat.' : 'Kolom Symbol/Emiten tidak ditemukan.', symbols.length ? 'ok' : 'error');
    };

    document.getElementById(`${APP_ID}-start`).onclick = async () => {
      const symbols = loadJson(SYMBOLS_KEY, []);
      if (!symbols.length) return setStatus('Impor CSV terlebih dahulu.', 'error');
      if (!capturedPageSymbol) return setStatus('Buka Broker Summary satu saham dan tunggu payload terlihat.', 'error');
      let job = getJob();
      if (!job.running && job.index >= symbols.length) {
        await clearStores();
        job = setJob({ index: 0, success: 0, failed: 0 });
      }
      if (!job.templateUrl || !job.templateSymbol || job.index === 0) {
        await clearStores();
        job = setJob({ running: true, index: 0, success: 0, failed: 0,
          templateUrl: location.href, templateSymbol: capturedPageSymbol });
      } else job = setJob({ running: true });
      setStatus('Batch dimulai. Browser akan berpindah halaman otomatis.');
      navigateToIndex(job.index);
    };

    document.getElementById(`${APP_ID}-stop`).onclick = () => {
      clearTimeout(timeoutId);
      setJob({ running: false });
      setStatus('Batch dihentikan. Data yang sudah tertangkap tetap tersimpan.');
      refreshPanel();
    };
    document.getElementById(`${APP_ID}-export`).onclick = exportAll;
    document.getElementById(`${APP_ID}-min`).onclick = () => {
      const body = document.getElementById(`${APP_ID}-body`);
      body.style.display = body.style.display === 'none' ? 'block' : 'none';
    };
    refreshPanel();
  }

  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    const response = await originalFetch.apply(this, args);
    try {
      const url = typeof args[0] === 'string' ? args[0] : args[0]?.url;
      if (isTargetUrl(url)) handlePayload(url, await response.clone().json());
    } catch (error) { console.warn('[SDE Broker v3] fetch capture gagal:', error); }
    return response;
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__sdeBrokerUrl = url;
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener('load', function () {
      try {
        if (isTargetUrl(this.__sdeBrokerUrl)) handlePayload(this.__sdeBrokerUrl, JSON.parse(this.responseText));
      } catch (error) { console.warn('[SDE Broker v3] XHR capture gagal:', error); }
    });
    return originalSend.apply(this, args);
  };

  window.addEventListener('DOMContentLoaded', () => {
    createPanel();
    armTimeout();
  });
  setInterval(createPanel, 1500);
})();
