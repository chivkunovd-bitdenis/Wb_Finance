/**
 * 🚀 WB FINANCE PRO v2.8 (TRACE DEBUG & STATE MANAGEMENT)
 * Архитектура: Глубокий перехват стека ошибок
 */

const CONFIG = {
    TAX_RATE: 0.06,
    SHEETS: { 
      USERS: "Users", 
      PNL: "DB_PnL", 
      RAW: "DB_Raw_Data", 
      ADS: "DB_Ads_Raw", 
      ARTICLES: "DB_Articles",
      GEMINI_KEY: 'AIzaSyCwGSFqFlb4O4oUZyvBAONMx4Q5A0qqr-o'
    },
    DB_FOLDER_ID: "114hoxrs14gF0n-wgSBO_DFRKTme2mbPz", 
    TEMPLATE_ID: "1iMYPFZaB-fB4jGfU8HpfQ5bfq89DPIB9mipSoYcy_-g"
  };
  const TIMEZONE = "Europe/Moscow";
  
  function doGet(e) {
    return HtmlService.createTemplateFromFile('Index').evaluate()
        .setTitle('WB Finance Analytics PRO')
        .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL)
        .addMetaTag('viewport', 'width=device-width, initial-scale=1');
  }
  
  function getUserDb(lic) {
    const cache = CacheService.getScriptCache();
    const cacheKey = 'db_id_' + String(lic).trim().toLowerCase();
    
    const cachedId = cache.get(cacheKey);
    if (cachedId) {
      try {
        return SpreadsheetApp.openById(cachedId);
      } catch(e) {
        cache.remove(cacheKey); // битый кэш — сбрасываем
      }
    }
  
    if (!CONFIG.DB_FOLDER_ID || CONFIG.DB_FOLDER_ID === "") {
      return SpreadsheetApp.getActiveSpreadsheet();
    }
  
    try {
      const folder = DriveApp.getFolderById(CONFIG.DB_FOLDER_ID);
      const fileName = "DB_" + String(lic).trim();
      const files = folder.getFilesByName(fileName);
      
      let ss;
      if (files.hasNext()) {
        ss = SpreadsheetApp.open(files.next());
      } else {
        const template = DriveApp.getFileById(CONFIG.TEMPLATE_ID);
        const newFile = template.makeCopy(fileName, folder);
        ss = SpreadsheetApp.open(newFile);
      }
      
      // Кэшируем ID на 10 минут — больше не лезем в Drive API
      cache.put(cacheKey, ss.getId(), 600);
      return ss;
  
    } catch(e) { 
      console.error("🚨 КРИТИЧЕСКИЙ СБОЙ DRIVE API: " + e.message);
      return SpreadsheetApp.getActiveSpreadsheet(); 
    }
  }
  
  /**
   * Проверка состояния системы (Strict Boolean Edition)
   * Гарантирует отсутствие null/undefined в ответе
   */
  function apiCheckSystemState(k) {
    try {
      const user = authenticateUser(k);
      const ss = getUserDb(k);
      const sPnl = ss.getSheetByName(CONFIG.SHEETS.PNL);
      return { 
        hasKeys: !!(user && user.wb_api_key && String(user.wb_api_key).length > 10), 
        hasData: !!(sPnl && sPnl.getLastRow() > 1) 
      };
    } catch (e) { 
      return { hasKeys: false, hasData: false }; 
    }
  }
  
  function runUpdateBatchWrapper(licenseKey) {
    try {
      const user = authenticateUser(licenseKey);
      if (!user || !user.wb_api_key) {
        return { success: false, error: "Системная ошибка: Пользователь или API-ключ не найден" };
      }
      
      runUpdateBatch(user.wb_api_key, licenseKey);
      const payload = apiGetDashboardPayload(licenseKey);
      return { success: true, payload: payload };
    } catch (e) { 
      return { success: false, error: e.message || String(e) }; 
    }
  }
  
  
  function fetchWbWithRrid(urlBase, k) {
    let allData = []; let currentRrid = 0; let hasMore = true;
    while (hasMore) {
      let url = urlBase + "&rrid=" + currentRrid; let res = fetchWb(url, k);
      if (res && res.length > 0) {
        allData = allData.concat(res);
        if (res.length >= 100000 && res[res.length - 1].rrid) currentRrid = res[res.length - 1].rrid;
        else hasMore = false;
      } else hasMore = false;
    }
    return allData;
  }
  
  function apiLoadArchiveChunk(licenseKey, dF, dT) {
    try {
      const user = authenticateUser(licenseKey);
      if (!user) throw new Error("Ключ не найден");
      
      // СТРОГАЯ ИНИЦИАЛИЗАЦИЯ SS
      const ss = getUserDb(licenseKey);
  
      // 1. Архив продаж
      const salesUrl = `https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod?dateFrom=${dF}&dateTo=${dT}&period=daily&limit=100000`;
      const salesData = fetchWbWithRrid(salesUrl, user.wb_api_key);
      
      if (salesData && salesData.length > 0) {
        updateDataBatch(ss, CONFIG.SHEETS.RAW, licenseKey, salesData, "date_from");
        updateArticlesCatalog(ss, salesData, licenseKey);
      }
  
      // 2. Архив рекламы
      const adsUrl = `https://advert-api.wildberries.ru/adv/v1/upd?from=${dF}&to=${dT}`;
      const adData = fetchWb(adsUrl, user.wb_api_key);
      
      if (adData && Array.isArray(adData) && adData.length > 0) {
        const ids = [...new Set(adData.map(a => a.advertId))];
        const details = getCampaignsDetails(user.wb_api_key, ids);
        let rows = [];
        
        adData.forEach(ad => {
          let nms = details.get(ad.advertId) || [];
          let sum = Number(ad.updSum) || 0; 
          let uTime = ad.updTime || ad.date || dT;
          
          if (nms.length > 0) {
            let splitSum = sum / nms.length;
            nms.forEach(nm => rows.push({ updTime: uTime, updSum: splitSum, campaignId: ad.advertId, nm_id: String(nm) }));
          } else {
            rows.push({ updTime: uTime, updSum: sum, campaignId: ad.advertId, nm_id: "" });
          }
        });
        updateDataBatch(ss, CONFIG.SHEETS.ADS, licenseKey, rows, "updTime");
      }
  
      // 3. Пересчет PnL строго в рамках дат текущего чанка архива
      updatePnlForPeriod(ss, licenseKey, dF, dT);
  
      // 4. Возврат
      const deltaPnl = apiGetPnlHistory(licenseKey).filter(r => r.d >= dF && r.d <= dT);
      const deltaSku = apiGetSkuHistory(licenseKey).filter(r => r.d >= dF && r.d <= dT);
      const articles = apiGetArticles(licenseKey);
  
      return { success: true, delta: { pnl: deltaPnl, sku: deltaSku, articles: articles } };
    } catch (e) { 
      throw new Error("Ошибка в чанке " + dF + ":\n" + (e.stack || e.message || String(e))); 
    }
  }
  
  function updatePnlForPeriod(ss, licenseKey, startDate, endDate) {
    const sales = getRowsAsObjectsByLicense(ss, CONFIG.SHEETS.RAW, licenseKey)
                    .filter(r => toIsoDate(r.date_from) >= startDate && toIsoDate(r.date_from) <= endDate);
    const ads = getRowsAsObjectsByLicense(ss, CONFIG.SHEETS.ADS, licenseKey)
                  .filter(r => toIsoDate(r.updTime || r.date) >= startDate && toIsoDate(r.updTime || r.date) <= endDate);
    
    const art = getRowsByLicense(ss, CONFIG.SHEETS.ARTICLES, licenseKey);
    const costMap = new Map(art.map(r => [String(r[1]), Number(r[4]) || 0]));
    
    const sByDate = groupByDate(sales, 'date_from'); 
    const aByDate = groupByDate(ads, 'updTime');
    const windowDates = [...new Set([...Object.keys(sByDate), ...Object.keys(aByDate)])].sort();
    
    let newPnlRows = windowDates.map(d => {
      let dayS = sByDate[d] || []; 
      let dayA = aByDate[d] || []; 
      let v = { sl:0, py:0, lg:0, pn:0, ad:0, rt:0, st:0, cg:0 };
      
      dayS.forEach(r => {
        let nm = String(r.nm_id); 
        let c = costMap.get(nm) || 0; 
        let qty = Number(r.quantity) || 1; 
        if (r.doc_type_name === "Продажа") { 
          v.sl += Number(r.retail_price)||0; 
          v.py += Number(r.ppvz_for_pay)||0; 
          v.cg += (c * qty); 
        } else if (r.doc_type_name === "Возврат") { 
          v.sl -= Number(r.retail_price)||0; 
          v.py -= Number(r.ppvz_for_pay)||0; 
          v.cg -= (c * qty); 
          v.rt++; 
        }
        v.lg += Number(r.delivery_rub || 0); 
        v.pn += (Number(r.penalty || 0) + Number(r.additional_payment || 0)); 
        v.st += Number(r.storage_fee || 0);
      });
      
      dayA.forEach(ad => v.ad += Number(ad.updSum) || 0);
      
      const tax = v.sl > 0 ? v.sl * CONFIG.TAX_RATE : 0; 
      const comm = v.sl - v.py; 
      const wbExp = comm + v.lg + v.pn + v.st; 
      const margin = v.sl - wbExp - v.ad - v.cg - tax;
      
      return [ licenseKey, d, v.sl, v.py, comm, (v.sl>0?comm/v.sl:0), v.lg, (v.sl>0?v.lg/v.sl:0), v.pn, v.rt, v.cg, CONFIG.TAX_RATE, tax, v.ad, (v.sl>0?v.ad/v.sl:0), v.st, (v.sl>0?v.st/v.sl:0), wbExp, (v.sl>0?wbExp/v.sl:0), margin, (v.sl>0?margin/v.sl:0), (v.cg>0?margin/v.cg:0) ];
    });
    
    const sheet = ss.getSheetByName(CONFIG.SHEETS.PNL);
    const existingData = sheet.getDataRange().getValues();
    const toReplaceRows = [];
    for (let i = 1; i < existingData.length; i++) {
      const rowNum = i + 1;
      const isSameUser = String(existingData[i][0]).trim().toLowerCase() === String(licenseKey).trim().toLowerCase();
      const rowDate = toIsoDate(existingData[i][1]);
      const isInWindow = rowDate >= startDate && rowDate <= endDate;
      if (isSameUser && isInWindow) toReplaceRows.push({ rowNum: rowNum, date: rowDate });
    }
    const toReplaceRowsFiltered = toReplaceRows.filter(function (x) { return x.rowNum !== 1; });
    if (toReplaceRowsFiltered.length !== newPnlRows.length) {
      let rowsToKeep = existingData.slice(1).filter(r => {
        let isSameUser = String(r[0]).trim().toLowerCase() === String(licenseKey).trim().toLowerCase();
        let rowDate = toIsoDate(r[1]);
        let isInWindow = rowDate >= startDate && rowDate <= endDate;
        return !(isSameUser && isInWindow);
      });
      let finalRows = [...rowsToKeep, ...newPnlRows].filter(r => Array.isArray(r) && r.length > 0);
      if (sheet.getLastRow() > 1) sheet.getRange(2, 1, sheet.getLastRow(), sheet.getLastColumn()).clearContent();
      if (finalRows.length > 0) {
        finalRows.sort((a, b) => (a[1] > b[1] ? 1 : -1));
        sheet.getRange(2, 1, finalRows.length, finalRows[0].length).setValues(finalRows);
      }
      return;
    }
    toReplaceRowsFiltered.sort((a, b) => a.date.localeCompare(b.date));
    const rowNumsAsc = toReplaceRowsFiltered.map(x => x.rowNum);
    const ranges = toContiguousRangesAsc(rowNumsAsc);
    const totalInRanges = ranges.reduce(function (sum, r) { return sum + r.numRows; }, 0);
    if (totalInRanges !== newPnlRows.length) {
      let rowsToKeep = existingData.slice(1).filter(r => {
        let isSameUser = String(r[0]).trim().toLowerCase() === String(licenseKey).trim().toLowerCase();
        let rowDate = toIsoDate(r[1]);
        let isInWindow = rowDate >= startDate && rowDate <= endDate;
        return !(isSameUser && isInWindow);
      });
      let finalRows = [...rowsToKeep, ...newPnlRows].filter(r => Array.isArray(r) && r.length > 0);
      if (sheet.getLastRow() > 1) sheet.getRange(2, 1, sheet.getLastRow(), sheet.getLastColumn()).clearContent();
      if (finalRows.length > 0) {
        finalRows.sort((a, b) => (a[1] > b[1] ? 1 : -1));
        sheet.getRange(2, 1, finalRows.length, finalRows[0].length).setValues(finalRows);
      }
      return;
    }
    let offset = 0;
    ranges.forEach(function (range) {
      const chunk = newPnlRows.slice(offset, offset + range.numRows);
      offset += range.numRows;
      if (chunk.length !== range.numRows) return;
      sheet.getRange(range.startRow, 1, range.numRows, newPnlRows[0].length).setValues(chunk);
    });
  }
  function apiGetDashboardPayload(licenseKey) { 
    try { 
      const ss = getUserDb(licenseKey);
      ensureFunnelSheet(ss);
      let pnl = apiGetPnlHistory(licenseKey) || [];
      const funnelRows = getRowsAsObjectsByLicense(ss, FUNNEL_SHEET, licenseKey);
      const dayOrderSum = {};
      funnelRows.forEach(function (r) { const d = toIsoDate(r.date); if (d) dayOrderSum[d] = (dayOrderSum[d] || 0) + (Number(r.orderSum) || 0); });
      pnl = pnl.map(function (r) { return Object.assign({}, r, { orderSum: dayOrderSum[r.d] || 0 }); });
      return { pnl: pnl, sku: apiGetSkuHistory(licenseKey) || [], articles: apiGetArticles(licenseKey) || [] }; 
    } catch (e) { throw new Error("Payload Error:\n" + (e.stack || e.message || String(e))); } 
  }
  
  function toIsoDate(v) { if (!v) return ""; if (v instanceof Date) return Utilities.formatDate(v, TIMEZONE, "yyyy-MM-dd"); return String(v).split('T')[0].split(' ')[0]; }
  function authenticateUser(k) {
    if (!k) return null;
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let s = ss.getSheetByName(CONFIG.SHEETS.USERS);
    if (!s) {
      const nameLower = (CONFIG.SHEETS.USERS || "").toString().toLowerCase();
      const found = ss.getSheets().find(function (sh) { return sh.getSheetName().toLowerCase() === nameLower; });
      if (found) s = found;
    }
    if (!s) return null;
    const data = s.getDataRange().getValues();
    const searchKey = String(k).trim().toLowerCase();
    for (let i = 1; i < data.length; i++) {
      if (String(data[i][0]).trim().toLowerCase() === searchKey) return { wb_api_key: data[i][1] };
    }
    return null;
  }
  
  function runUpdateBatch(key, licenseKey) {
    try {
      const ss = getUserDb(licenseKey); 
      if (!ss) throw new Error("Не удалось получить доступ к персональной БД");
      
      const today = new Date();
      const yesterday = new Date(today.getTime() - 86400000);
      const endStr = toIsoDate(yesterday);
  
      const lastSalesDate = getLastRecordedDateForUser(ss, CONFIG.SHEETS.RAW, licenseKey, "date_from");
      const lastStr = lastSalesDate ? toIsoDate(lastSalesDate) : "";
      if (lastStr && lastStr >= endStr) {
        return true;
      }
  
      const salesStartDate = lastSalesDate 
        ? new Date(lastSalesDate.getTime() + 86400000)
        : new Date(today.getTime() - (30 * 86400000));
      const salesStartStr = toIsoDate(salesStartDate);
      const adsStartDate = new Date(yesterday.getTime() - (2 * 86400000));
      const adsStartStr = toIsoDate(adsStartDate);
  
      if (salesStartStr <= endStr) {
        const salesUrl = `https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod?dateFrom=${salesStartStr}&dateTo=${endStr}&period=daily&limit=100000`;
        const salesData = fetchWbWithRrid(salesUrl, key);
        if (salesData && salesData.length > 0) {
          updateDataBatch(ss, CONFIG.SHEETS.RAW, licenseKey, salesData, "date_from");
          updateArticlesCatalog(ss, salesData, licenseKey);
        }
      }
  
      const adsUrl = `https://advert-api.wildberries.ru/adv/v1/upd?from=${adsStartStr}&to=${endStr}`;
      const adData = fetchWb(adsUrl, key);
      if (adData && Array.isArray(adData) && adData.length > 0) {
        const ids = [...new Set(adData.map(a => a.advertId))];
        const details = getCampaignsDetails(key, ids);
        let rows = [];
        adData.forEach(ad => {
          let nms = details.get(ad.advertId) || [];
          let sum = Number(ad.updSum) || 0;
          let uTime = ad.updTime || ad.date || endStr;
          if (nms.length > 0) {
            let splitSum = sum / nms.length;
            nms.forEach(nm => rows.push({ updTime: uTime, updSum: splitSum, campaignId: ad.advertId, nm_id: String(nm) }));
          } else {
            rows.push({ updTime: uTime, updSum: sum, campaignId: ad.advertId, nm_id: "" });
          }
        });
        updateDataBatch(ss, CONFIG.SHEETS.ADS, licenseKey, rows, "updTime");
      }
  
      const pnlStartStr = salesStartStr < adsStartStr ? salesStartStr : adsStartStr;
      updatePnlForPeriod(ss, licenseKey, pnlStartStr, endStr);
  
      return true;
  
    } catch (e) {
      throw new Error("Сбой в конвейере данных: " + e.message);
    }
  }
  
  
  /** Группирует отсортированный по возрастанию массив номеров строк в непрерывные диапазоны. */
  function toContiguousRangesAsc(sortedRowNumsAsc) {
    if (!sortedRowNumsAsc || sortedRowNumsAsc.length === 0) return [];
    const ranges = [];
    let start = sortedRowNumsAsc[0];
    let end = start;
    for (let i = 1; i < sortedRowNumsAsc.length; i++) {
      const row = sortedRowNumsAsc[i];
      if (row === end + 1) end = row;
      else {
        ranges.push({ startRow: start, numRows: end - start + 1 });
        start = row; end = row;
      }
    }
    ranges.push({ startRow: start, numRows: end - start + 1 });
    return ranges;
  }
  
  // ОПТИМИЗАЦИЯ: замена только строк по датам (по месту). getRange(row,col,numRows,numCols).
  function updateDataBatch(ss, sn, l, r, df) {
    const s = ss.getSheetByName(sn);
    const data = s.getDataRange().getValues();
    const h = data[0];
    const dIdx = h.indexOf(df);
    const lIdx = h.indexOf("license_key");
    const nDates = new Set(r.map(i => toIsoDate(i[df] || i.date)));
    const add = r.map(i => h.map(f => (f === 'license_key' ? l : (i[f] ?? ""))));
    const toReplaceRows = [];
    for (let i = 1; i < data.length; i++) {
      const rowNum = i + 1;
      if (String(data[i][lIdx]).trim().toLowerCase() === String(l).trim().toLowerCase() && nDates.has(toIsoDate(data[i][dIdx]))) {
        toReplaceRows.push(rowNum);
      }
    }
    const toReplaceRowsFiltered = toReplaceRows.filter(function (rn) { return rn !== 1; });
    if (toReplaceRowsFiltered.length !== add.length) {
      let keep = data.slice(1).filter(i => String(i[lIdx]) !== String(l) || !nDates.has(toIsoDate(i[dIdx])));
      if (s.getLastRow() > 1) s.getRange(2, 1, s.getLastRow(), s.getLastColumn()).clearContent();
      if (add.length + keep.length > 0) s.getRange(2, 1, keep.length + add.length, h.length).setValues([...keep, ...add]);
      return;
    }
    toReplaceRowsFiltered.sort((a, b) => a - b);
    const datesInOrder = toReplaceRowsFiltered.map(rowNum => toIsoDate(data[rowNum - 1][dIdx]));
    const addByDate = {};
    add.forEach(row => {
      const d = toIsoDate(row[dIdx]);
      if (!addByDate[d]) addByDate[d] = [];
      addByDate[d].push(row);
    });
    const reorderedAdd = datesInOrder.map(d => (addByDate[d] && addByDate[d].length) ? addByDate[d].shift() : null);
    if (reorderedAdd.length !== toReplaceRowsFiltered.length || reorderedAdd.some(function (x) { return x == null; })) {
      let keep = data.slice(1).filter(i => String(i[lIdx]) !== String(l) || !nDates.has(toIsoDate(i[dIdx])));
      if (s.getLastRow() > 1) s.getRange(2, 1, s.getLastRow(), s.getLastColumn()).clearContent();
      if (add.length + keep.length > 0) s.getRange(2, 1, keep.length + add.length, h.length).setValues([...keep, ...add]);
      return;
    }
    const ranges = toContiguousRangesAsc(toReplaceRowsFiltered);
    let offset = 0;
    ranges.forEach(function (range) {
      const chunk = reorderedAdd.slice(offset, offset + range.numRows);
      offset += range.numRows;
      if (chunk.length !== range.numRows) return;
      s.getRange(range.startRow, 1, range.numRows, h.length).setValues(chunk);
    });
  }
  const FUNNEL_SHEET = "DB_Funnel_Daily";
  function ensureFunnelSheet(ss) {
    let s = ss.getSheetByName(FUNNEL_SHEET);
    const targetHeader = ["license_key", "nmId", "vendorCode", "title", "date", "openCount", "cartCount", "orderCount", "orderSum", "buyoutPercent", "cr1", "cr2", "updatedAt"];
    
    // Если листа нет — создаем с целевым хедером
    if (!s) {
      s = ss.insertSheet(FUNNEL_SHEET);
      s.getRange(1, 1, 1, targetHeader.length).setValues([targetHeader]);
      return;
    }
    
    // Лист есть — проверяем, есть ли уже целевой хедер (orderSum и остальные поля)
    const data = s.getDataRange().getValues();
    if (!data || data.length === 0) {
      s.getRange(1, 1, 1, targetHeader.length).setValues([targetHeader]);
      return;
    }
    
    const header = data[0];
    const hasOrderSum = header.indexOf("orderSum") !== -1;
    const hasVendorCode = header.indexOf("vendorCode") !== -1;
    const hasTitle = header.indexOf("title") !== -1;
    if (hasOrderSum && hasVendorCode && hasTitle && header.length === targetHeader.length) {
      return;
    }
    
    // Миграция старого формата:
    // license_key,nmId,date,openCount,cartCount,orderCount,buyoutPercent,cr1,cr2,updatedAt
    const idx = function (name) { return header.indexOf(name); };
    const iLicense = idx("license_key");
    const iNmId = idx("nmId");
    const iDate = idx("date");
    const iOpen = idx("openCount");
    const iCart = idx("cartCount");
    const iOrder = idx("orderCount");
    const iBuyout = idx("buyoutPercent");
    const iCr1 = idx("cr1");
    const iCr2 = idx("cr2");
    const iUpdated = idx("updatedAt");
    
    let newData = [];
    newData.push(targetHeader);
    
    for (let r = 1; r < data.length; r++) {
      const row = data[r];
      let n = [];
      n[0] = iLicense !== -1 ? row[iLicense] : "";
      n[1] = iNmId !== -1 ? row[iNmId] : "";
      n[2] = ""; // vendorCode (ранее не хранился)
      n[3] = ""; // title (ранее не хранился)
      n[4] = iDate !== -1 ? row[iDate] : "";
      n[5] = iOpen !== -1 ? row[iOpen] : 0;
      n[6] = iCart !== -1 ? row[iCart] : 0;
      n[7] = iOrder !== -1 ? row[iOrder] : 0;
      n[8] = 0; // orderSum (старые данные без суммы заказов)
      n[9] = iBuyout !== -1 ? row[iBuyout] : 0;
      n[10] = iCr1 !== -1 ? row[iCr1] : 0;
      n[11] = iCr2 !== -1 ? row[iCr2] : 0;
      n[12] = iUpdated !== -1 ? row[iUpdated] : "";
      newData.push(n);
    }
    
    s.clear();
    s.getRange(1, 1, newData.length, targetHeader.length).setValues(newData);
  }
  function getRowsByLicense(ss, sn, k) { 
    const s = ss.getSheetByName(sn); 
    if (!s) return [];
    return s.getDataRange().getValues().slice(1).filter(r => String(r[0]).trim().toLowerCase() === String(k).trim().toLowerCase()); 
  }
  function getRowsAsObjectsByLicense(ss, sn, k) { const s = ss.getSheetByName(sn); const d = s.getDataRange().getValues(); const h = d[0]; return d.slice(1).filter(r => String(r[0]).trim().toLowerCase() === String(k).trim().toLowerCase()).map(r => { let o = {}; h.forEach((key, i) => o[key] = r[i]); return o; }); }
  function groupByDate(d, f) { let g = {}; d.forEach(i => { const k = toIsoDate(i[f]); if (k) { if (!g[k]) g[k] = []; g[k].push(i); } }); return g; }
  function getLastRecordedDateForUser(ss, sn, k, df) { const s = ss.getSheetByName(sn); if (!s || s.getLastRow() < 2) return null; const h = s.getRange(1,1,1,s.getLastColumn()).getValues()[0]; const idx = h.indexOf(df); const d = s.getDataRange().getValues(); let max = null; for (let i=1; i<d.length; i++) if (String(d[i][0]).trim().toLowerCase() === String(k).trim().toLowerCase()) { let dt = new Date(toIsoDate(d[i][idx])); if (!isNaN(dt.getTime()) && (!max || dt > max)) max = dt; } return max; }
  function updateArticlesCatalog(ss, d, k) { 
    const s = ss.getSheetByName(CONFIG.SHEETS.ARTICLES); 
    if (!s) return;
    const ex = new Set(getRowsByLicense(ss, CONFIG.SHEETS.ARTICLES, k).map(r => String(r[1]))); 
    let nw = []; 
    d.forEach(i => { 
      if (!ex.has(String(i.nm_id))) { 
        nw.push([k, i.nm_id, i.sa_name, i.subject_name, 0]); 
        ex.add(String(i.nm_id)); 
      }
    }); 
    if (nw.length) s.getRange(s.getLastRow() + 1, 1, nw.length, 5).setValues(nw); 
  }
  function apiGetPnlHistory(licenseKey) { 
    const ss = getUserDb(licenseKey);
    return getRowsByLicense(ss, CONFIG.SHEETS.PNL, licenseKey).map(r => ({ d: toIsoDate(r[1]), s: Number(r[2])||0, c: Number(r[4])||0, l: Number(r[6])||0, p: Number(r[8])||0, cg: Number(r[10])||0, a: Number(r[13])||0, st: Number(r[15])||0, m: Number(r[19])||0 })); 
  }
  function apiGetSkuHistory(licenseKey) {
    const ss = getUserDb(licenseKey); 
    const sales = getRowsAsObjectsByLicense(ss, CONFIG.SHEETS.RAW, licenseKey); 
    const ads = getRowsAsObjectsByLicense(ss, CONFIG.SHEETS.ADS, licenseKey);
    const art = getRowsByLicense(ss, CONFIG.SHEETS.ARTICLES, licenseKey); 
    const costMap = new Map(art.map(r => [String(r[1]), Number(r[4]) || 0])); 
    const nameMap = new Map(art.map(r => [String(r[1]), String(r[2])]));
    let skuMap = {}; 
    sales.forEach(r => {
      const d = toIsoDate(r.date_from); if (!d) return; const nm = String(r.nm_id); const key = d + "_" + nm; const cost = costMap.get(nm) || 0; let qty = Number(r.quantity) || 1; 
      if (!skuMap[key]) skuMap[key] = { d: d, nm: nm, n: nameMap.get(nm) || "Товар " + nm, s:0, c:0, l:0, p:0, a:0, cg: 0, orderSum: 0 };
      if (r.doc_type_name === "Продажа") { skuMap[key].s += Number(r.retail_price)||0; skuMap[key].c += (Number(r.retail_price)||0) - (Number(r.ppvz_for_pay)||0); skuMap[key].cg += (cost * qty); }
      else if (r.doc_type_name === "Возврат") { skuMap[key].s -= Number(r.retail_price)||0; skuMap[key].c -= (Number(r.retail_price)||0) - (Number(r.ppvz_for_pay)||0); skuMap[key].cg -= (cost * qty); }
      skuMap[key].l += Number(r.delivery_rub)||0; skuMap[key].p += (Number(r.penalty)||0) + (Number(r.additional_payment)||0);
    });
    ads.forEach(ad => {
      const d = toIsoDate(ad.updTime); if (!d) return; const nm = String(ad.nm_id).trim(); if (!nm || nm === "undefined" || nm === "null" || nm === "") return; 
      const key = d + "_" + nm; if (!skuMap[key]) skuMap[key] = { d: d, nm: nm, n: nameMap.get(nm) || "Товар " + nm, s:0, c:0, l:0, p:0, a:0, cg: 0, orderSum: 0 };
      skuMap[key].a += Number(ad.updSum) || 0;
    });
    const funnelSheet = ss.getSheetByName(FUNNEL_SHEET);
    if (funnelSheet) {
      getRowsAsObjectsByLicense(ss, FUNNEL_SHEET, licenseKey).forEach(function (r) {
        const d = toIsoDate(r.date); if (!d) return;
        const nm = String(r.nmId != null ? r.nmId : r.nm_id || "");
        const key = d + "_" + nm;
        if (skuMap[key]) skuMap[key].orderSum += Number(r.orderSum) || 0;
      });
    }
    return Object.values(skuMap).map(v => { const tax = v.s > 0 ? v.s * CONFIG.TAX_RATE : 0; const margin = v.s - v.c - v.l - v.p - v.a - v.cg - tax; return { d: v.d, nm: v.nm, n: v.n, s: v.s, c: v.c, l: v.l, p: v.p, a: v.a, cg: v.cg, m: margin, orderSum: v.orderSum || 0, img: `https://basket-01.wbbasket.ru/vol${String(v.nm).substring(0,4)}/part${String(v.nm).substring(0,6)}/${String(v.nm)}/images/c246x328/1.jpg` }; });
  }
  function apiGetArticles(k) { 
    const ss = getUserDb(k);
    return getRowsByLicense(ss, CONFIG.SHEETS.ARTICLES, k).map(r => ({ nm_id: String(r[1]), sa_name: String(r[2]), cost: Number(r[4]) || 0 })); 
  }
  function apiSaveArticlesCost(k, i) { 
    try {
      const ss = getUserDb(k); 
      const s = ss.getSheetByName(CONFIG.SHEETS.ARTICLES); 
      if (!s) throw new Error("Лист конфигурации себестоимости не найден");
      const d = s.getDataRange().getValues(); 
      
      i.forEach(it => { 
        for (let r=1; r<d.length; r++) {
          if (String(d[r][0]).trim().toLowerCase()===String(k).trim().toLowerCase() && String(d[r][1])===String(it.nm_id)) {
            s.getRange(r+1, 5).setValue(it.cost); 
          }
        }
      }); 
      
      // Пересчет всей истории при смене себестоимости через новую функцию
      updatePnlForPeriod(ss, k, '2020-01-01', toIsoDate(new Date())); 
      
      return { success: true };
    } catch (e) {
      return { success: false, error: e.message || String(e) };
    }
  }
  function saveSettings(lic, wb) { const s = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Users"); const data = s.getDataRange().getValues(); for (let i=1; i<data.length; i++) if (String(data[i][0]).trim().toLowerCase() === String(lic).trim().toLowerCase()) { s.getRange(i+1, 2).setValue(wb); return { success: true }; } throw new Error("Лицензия не найдена"); }
  
  
  // Вспомогательная функция для формирования ISO даты (YYYY-MM-DD)
  // Объявлена строго до первого вызова
  function getIsoDateForTest(daysAgo) {
    const date = new Date();
    date.setDate(date.getDate() - daysAgo);
    return date.toISOString().split('T')[0];
  }
  
  // Изолированная функция для дебага API ВБ
  function testWbApiConnection() {
    // 1. ПОДСТАВЬ СЮДА СВОЙ ТОКЕН СТАТИСТИКИ
    const API_TOKEN = "eyJhbGciOiJFUzI1NiIsImtpZCI6IjIwMjUwOTA0djEiLCJ0eXAiOiJKV1QifQ.eyJhY2MiOjEsImVudCI6MSwiZXhwIjoxNzg2NDA0MDE1LCJpZCI6IjAxOWM0MjIxLTc4YjYtN2RmZC1hZDg4LTEzMWQ4NjUzNWM5NyIsImlpZCI6MTk5NTY2NjksIm9pZCI6MTM2OTIyMiwicyI6MTA3Mzc1Nzk1MCwic2lkIjoiZmEwMmI4MDAtYjNiMS00NDg5LThiNzItYWJjZTc4MGNkNjg3IiwidCI6ZmFsc2UsInVpZCI6MTk5NTY2Njl9.0-PgA06H3hmkmxBzn2l5RtBOGx4u3hHGTNG3cEc4-D_ZP7KlolS_FU7MGPM_L7-_0OhRYMP_UPU3VsXkNnTY6A"; 
    
    // 2. Строгий ISO формат даты (берем, например, за последние 5 дней)
    const dateFrom = getIsoDateForTest(5); 
    
    // 3. Эндпоинт статистики продаж (чаще всего падает именно он из-за объемов). 
    // Если у тебя падал /api/v5/supplier/reportDetailByPeriod - просто замени URL.
    const baseUrl = "https://statistics-api.wildberries.ru/api/v1/supplier/sales";
    
    // 4. Обязательные параметры по нашему стандарту разработки
    const limit = 100000;
    const rrid = 0; 
    
    const url = `${baseUrl}?dateFrom=${dateFrom}&limit=${limit}&rrid=${rrid}`;
    
    const options = {
      method: "get",
      headers: {
        "Authorization": API_TOKEN,
        "Content-Type": "application/json"
      },
      // КРИТИЧНО ДЛЯ ДЕБАГА: не позволяем GAS упасть, заставляем читать ответ
      muteHttpExceptions: true 
    };
    
    Logger.log("=== СТАРТ ТЕСТА ===");
    Logger.log("URL: " + url);
    Logger.log("Ожидаем ответ от Wildberries...");
    
    try {
      // Делаем синхронный вызов
      const response = UrlFetchApp.fetch(url, options);
      const responseCode = response.getResponseCode();
      const responseText = response.getContentText();
      
      Logger.log("=== РЕЗУЛЬТАТ ===");
      Logger.log("HTTP Статус код: " + responseCode);
      
      if (responseCode === 200) {
        Logger.log("✅ УСПЕХ: Сервер ВБ ответил 200 OK.");
        const data = JSON.parse(responseText);
        Logger.log("📦 Получено записей в первом пакете: " + (data ? data.length : 0));
      } else {
        Logger.log("❌ ОШИБКА API ВБ!");
        // Выводим сырой ответ сервера (часто там HTML страница от nginx с ошибкой 502/504)
        Logger.log("Тело ответа: " + responseText.substring(0, 500) + "... (обрезано для лога)");
      }
      
    } catch (e) {
      // Сюда попадем, только если упадут сами сервера Google или отвалится таймаут UrlFetchApp (60 секунд)
      Logger.log("🚨 Системная ошибка GAS: " + e.message);
    }
  }
  
  function debugAdsFilter() {
    const licenseToTest = "ВСТАВЬ_СЮДА_СВОЙ_ЛИЦЕНЗИОННЫЙ_КЛЮЧ";
    const ss = getUserDb(licenseToTest);
    const sheet = ss.getSheetByName(CONFIG.SHEETS.ADS);
    
    if (!sheet) {
      Logger.log("🚨 ОШИБКА: Лист " + CONFIG.SHEETS.ADS + " не найден!");
      return;
    }
    
    const data = sheet.getDataRange().getValues();
    Logger.log("📊 Всего строк в таблице рекламы: " + data.length);
    
    // Берем первые 3 строки данных (пропуская заголовок)
    const sample = data.slice(1, 4);
    
    sample.forEach((row, index) => {
      const valInColA = row[0];
      const stringVal = String(valInColA).trim().toLowerCase();
      const keyToCompare = String(licenseToTest).trim().toLowerCase();
      const isMatch = (stringVal === keyToCompare);
      
      Logger.log(`--- Строка ${index + 2} ---`);
      Logger.log(`Значение в столбце А: "${valInColA}" (Тип: ${typeof valInColA})`);
      Logger.log(`Длина строки: ${String(valInColA).length}`);
      Logger.log(`Результат сравнения: ${isMatch}`);
      
      if (!isMatch && String(valInColA).includes(licenseToTest)) {
         Logger.log("⚠️ ВНИМАНИЕ: Похоже на скрытые символы или пробелы!");
      }
    });
  }
  
  // 2. ИСПРАВЛЕНИЕ РЕГРЕССИИ UNIT-ТЕСТА (API Contract Wrapper)
  // Обертка сохраняет совместимость со старыми тестами и внешней логикой
  function apiLoadArchive(licenseKey, dF, dT) {
    return apiLoadArchiveChunk(licenseKey, dF, dT);
  }
  
  // === МОДУЛЬ АВТОРИЗАЦИИ (SaaS LOG/PASS) ===
  // Добавлено без изменения существующих структур
  
  function apiRegister(email, pass, wbKey) {
    try {
      const e = String(email).trim().toLowerCase();
      const p = String(pass).trim();
      const w = String(wbKey).trim();
  
      if (!e || !p || !w) return { success: false, message: "Заполните все поля" };
  
      const ss = SpreadsheetApp.getActiveSpreadsheet();
      const sheet = ss.getSheetByName(CONFIG.SHEETS.USERS);
      if (!sheet) return { success: false, message: "Системная ошибка: лист Users не найден" };
  
      const data = sheet.getDataRange().getValues();
      for (let i = 1; i < data.length; i++) {
        if (String(data[i][0]).trim().toLowerCase() === e) {
          return { success: false, message: "Пользователь с таким email уже существует" };
        }
      }
  
      // Новый пользователь всегда disabled — ты активируешь вручную
      sheet.appendRow([e, w, p, 'false']);
  
      return { success: true, message: "Аккаунт создан. Ожидайте активации." };
    } catch (err) {
      return { success: false, message: "Ошибка сервера: " + err.message };
    }
  }
  
  function apiLogin(email, pass) {
    try {
      const e = String(email).trim().toLowerCase();
      const p = String(pass).trim();
  
      if (!e || !p) return { success: false, message: "Заполните логин и пароль" };
  
      const ss = SpreadsheetApp.getActiveSpreadsheet();
      const sheet = ss.getSheetByName(CONFIG.SHEETS.USERS);
      if (!sheet) return { success: false, message: "Системная ошибка: лист Users не найден" };
  
      const data = sheet.getDataRange().getValues();
  
      for (let i = 1; i < data.length; i++) {
        if (String(data[i][0]).trim().toLowerCase() === e) {
          if (String(data[i][2]).trim() !== p) {
            return { success: false, message: "Неверный логин или пароль" };
          }
  
          // Проверяем столбец D — enabled
          const enabled = String(data[i][3]).trim().toLowerCase();
          if (enabled !== 'true') {
            return { success: false, message: "Аккаунт ожидает активации. Свяжитесь с поддержкой." };
          }
  
          return { success: true, licenseKey: e };
        }
      }
  
      return { success: false, message: "Пользователь не найден" };
    } catch (err) {
      return { success: false, message: "Ошибка сервера: " + err.message };
    }
  }
  
  /**
   * 🕵️ ГЛУБОКАЯ ДИАГНОСТИКА СОЗДАНИЯ ТАБЛИЦ
   * Запусти эту функцию через кнопку "Выполнить" вверху
   */
  function FORCE_TEST_SYNC() {
    const testEmail = "ВСТАВЬ_СВОЙ_EMAIL"; 
    const rawId = CONFIG.TEMPLATE_ID;
    
    console.log("=== 🔍 ДИАГНОСТИКА ШАБЛОНА ===");
    console.log("ID из конфига: [" + rawId + "]");
    console.log("Длина ID: " + String(rawId).length + " символов");
    console.log("Тип данных: " + typeof rawId);
  
    try {
      const file = DriveApp.getFileById(String(rawId).trim());
      console.log("✅ Файл опознан DriveApp");
      console.log("Имя файла: " + file.getName());
      console.log("MIME тип: " + file.getMimeType());
      
      // Если дошли сюда, пробуем getUserDb
      const ss = getUserDb(testEmail);
      console.log("🎉 Финальный ID таблицы: " + ss.getId());
      
    } catch (e) {
      console.error("❌ СБОЙ НА ЭТАПЕ ОПОЗНАНИЯ ФАЙЛА:");
      console.error("Сообщение: " + e.message);
      
      if (e.message.includes("Unexpected error")) {
        console.warn("💡 СОВЕТ: Попробуй создать копию шаблона (Файл -> Создать копию) и вставить ID НОВОЙ копии в CONFIG.");
      }
    }
  }
  function fetchWb(url, k) { try { const r = UrlFetchApp.fetch(url, { headers: { 'Authorization': k }, muteHttpExceptions: true }); return r.getResponseCode() === 200 ? JSON.parse(r.getContentText()) : []; } catch(e) { return []; } }
  
  
  // Вспомогательная функция для получения артикулов внутри рекламных кампаний
  function getCampaignsDetails(key, ids) { 
    let map = new Map(); 
    if (!ids || !ids.length) return map; 
    for (let i = 0; i < ids.length; i += 50) { 
      const chunk = ids.slice(i, i + 50); 
      const r = UrlFetchApp.fetch(`https://advert-api.wildberries.ru/api/advert/v2/adverts?ids=${chunk.join(',')}`, { headers: { 'Authorization': key }, muteHttpExceptions: true }); 
      if (r.getResponseCode() === 200) { 
        const json = JSON.parse(r.getContentText()); 
        (json.adverts || json).forEach(c => { 
          let nms = []; 
          if (c.nm_settings) c.nm_settings.forEach(s => nms.push(s.nm_id)); 
          map.set(c.id || c.advertId, nms); 
        }); 
      } 
      Utilities.sleep(300); 
    } 
    return map; 
  }
  
  /**
   * Ядро ИИ-аналитики: отправка данных в Gemini
   */
  /**
   * Ядро ИИ-аналитики: отправка данных в Gemini (Режим Глубокой Отладки)
   */
  /**
   * Ядро ИИ-аналитики: отправка данных в Gemini (Режим Глубокой Отладки)
   */
  function apiGetAiAnalysis(licenseKey, dataPayload) {
    try {
      Logger.log("=== СТАРТ apiGetAiAnalysis ===");
      const payload = dataPayload || {}; 
      
      const appConfig = getAppConfig();
      const apiKey = appConfig.GEMINI_KEY;
      
      if (!apiKey || String(apiKey).trim() === "") {
        return { success: false, error: "Ключ API не установлен." };
      }
  
      const prompt = payload.customPrompt || "Проанализируй данные магазина.";
  
      const requestBody = {
        contents: [{ parts: [{ text: prompt }] }]
      };
  
      const options = {
        method: "post",
        contentType: "application/json",
        payload: JSON.stringify(requestBody),
        muteHttpExceptions: true
      };
  
      const url = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key=${apiKey}`;
      const response = UrlFetchApp.fetch(url, options);
      const responseCode = response.getResponseCode();
      const responseText = response.getContentText();
      
      if (responseCode !== 200) {
        return { success: false, error: "API Error HTTP " + responseCode + ": " + responseText };
      }
      
      const json = JSON.parse(responseText);
      
      if (!json.candidates || !json.candidates[0].content) {
        return { success: false, error: "Неверный формат ответа от ИИ. Получено: " + responseText };
      }
      
      return { success: true, text: json.candidates[0].content.parts[0].text };
      
    } catch (e) {
      return { success: false, error: "CRASH: " + e.toString(), stack: e.stack };
    }
  }
  function getAppConfig() {
    // Возвращаем глобальный объект CONFIG
    // Извлекаем GEMINI_KEY из вложенной структуры SHEETS для безопасного доступа
    return {
      ...CONFIG,
      GEMINI_KEY: CONFIG.GEMINI_KEY || (CONFIG.SHEETS && CONFIG.SHEETS.GEMINI_KEY) || ""
    };
  }
  function chunkArray(arr, size) {
    let res = [];
    for (let i = 0; i < arr.length; i += size) {
      res.push(arr.slice(i, i + size));
    }
    return res;
  }
  
  function getUniqueNmIdsByLogin(ss, login) {
    // ИСПРАВЛЕНО: Теперь смотрим в правильную базу артикулов
    const sheet = ss.getSheetByName("DB_Articles"); 
    if (!sheet) {
      Logger.log("Вкладка DB_Articles не найдена!");
      return [];
    }
    
    const data = sheet.getDataRange().getValues();
    if (data.length < 2) return [];
    
    const headers = data[0];
    // Ищем колонку по старому системному имени, но сравниваем с переданным логином
  let loginIdx = headers.indexOf("login");
  if (loginIdx === -1) {
    loginIdx = headers.indexOf("license_key"); // Fallback на старое название
  }
    
    // ВАЖНО: Проверь, как у тебя называется колонка с артикулом в DB_Articles. 
    // Обычно это "nm_id", но если у тебя "nmId" или "Артикул ВБ", поменяй название в кавычках ниже.
    let nmIdx = headers.indexOf("nm_id"); 
    if (nmIdx === -1) nmIdx = headers.indexOf("nmId"); // Запасной вариант
    
    if (loginIdx === -1 || nmIdx === -1) {
      Logger.log("В DB_Articles не найдены колонки login или nm_id");
      return [];
    }
    
    const nmIds = new Set();
    for (let i = 1; i < data.length; i++) {
      if (String(data[i][loginIdx]).trim().toLowerCase() === String(login).trim().toLowerCase()) {
        let nm = Number(data[i][nmIdx]);
        if (nm) nmIds.add(nm);
      }
    }
    
    const result = Array.from(nmIds);
    Logger.log("Найдено уникальных артикулов: " + result.length);
    return result;
  }
  
  
  
  
  function TEST_FUNNEL() {
    const testLogin = "тестовый 7";
    Logger.log("=== СТАРТ ТЕСТА СИНХРОНИЗАЦИИ ВОРОНКИ ===");
    Logger.log("Логин: " + testLogin);
    
    const result = apiSyncFunnelData(testLogin);
    
    Logger.log("Статус: " + result.status);
    Logger.log("Сообщение: " + result.message);
    Logger.log("Возвращено записей: " + (result.data ? result.data.length : 0));
    
    if (result.data && result.data.length > 0) {
      Logger.log("Пример первой записи:");
      Logger.log(JSON.stringify(result.data[0], null, 2));
    }
    Logger.log("=== КОНЕЦ ТЕСТА ===");
  }
  
  /**
   * Синхронизация данных воронки продаж v3.
   * WB отдаёт только последние 7 дней — запрашиваем один раз это окно, накапливаем историю в листе.
   */
  function apiSyncFunnelData(licenseKey, startDate, endDate) {
    try {
      const user = authenticateUser(licenseKey);
      if (!user || !user.wb_api_key) throw new Error("API-ключ пользователя не найден");
      const ss = getUserDb(licenseKey);
      ensureFunnelSheet(ss);
      const tz = Session.getScriptTimeZone();
      const today = new Date();
      const targetEnd = new Date(today.getTime());
      targetEnd.setDate(today.getDate() - 1);
      const targetStart = new Date(targetEnd.getTime());
      targetStart.setDate(targetEnd.getDate() - 6);
      const finalStart = Utilities.formatDate(targetStart, tz, "yyyy-MM-dd");
      const finalEnd = Utilities.formatDate(targetEnd, tz, "yyyy-MM-dd");
      Logger.log("🚀 Запуск воронки v3 для окна 7 дней: " + finalStart + " - " + finalEnd);
      const nmIds = getUniqueNmIdsByLogin(ss, licenseKey);
      
      if (!nmIds || nmIds.length === 0) {
        Logger.log("⚠️ Нет артикулов для синхронизации воронки.");
        return { success: true, data: [] }; 
      }

      const nmChunks = chunkArray(nmIds, 20);
      let newRows = [];
      const updateTime = new Date().toISOString();
      const url = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products/history";
  
      for (const chunk of nmChunks) {
        const payload = {
          "selectedPeriod": {
            "start": finalStart,
            "end": finalEnd
          },
            "nmIds": chunk,
            "skipDeletedNm": true,
            "aggregationLevel": "day"
          };
    
          const options = {
            method: "post",
            headers: {
              "Authorization": user.wb_api_key,
              "Content-Type": "application/json"
            },
            payload: JSON.stringify(payload),
            muteHttpExceptions: true
          };
    
          const response = UrlFetchApp.fetch(url, options);
          const responseCode = response.getResponseCode();
          
          if (responseCode === 200) {
            const json = JSON.parse(response.getContentText());
            const items = Array.isArray(json) ? json : (json.data || json.cards || []);
            if (items.length > 0 && (items[0].history || []).length > 0 && newRows.length === 0) {
              Logger.log("WB воронка: ключи дня — " + Object.keys(items[0].history[0]).join(", "));
            }
            items.forEach(item => {
              const id = item.nmId || (item.product ? item.product.nmId : 0);
              const history = item.history || [];
    
              const vendorCode = String(item.vendorCode != null ? item.vendorCode : (item.product && item.product.vendorCode) || "").slice(0, 500);
              const title = String(item.title != null ? item.title : (item.product && item.product.title) || "").slice(0, 1000);
              history.forEach(h => {
                const sumRub = Number(h.orderSum) || 0;
                newRows.push({
                  license_key: licenseKey,
                  nmId: Number(id),
                  vendorCode: vendorCode,
                  title: title,
                  date: toIsoDate(h.date || h.dt),
                  openCount: Number(h.openCount) || 0,
                  cartCount: Number(h.cartCount) || 0,
                  orderCount: Number(h.orderCount) || 0,
                  orderSum: sumRub,
                  buyoutPercent: Number(h.buyoutPercent) || 0,
                  cr1: Number(h.addToCartConversion || h.cr1) || 0,
                  cr2: Number(h.cartToOrderConversion || h.cr2) || 0,
                  updatedAt: updateTime
                });
              });
            });
          } else {
            Logger.log("⚠️ Ошибка API WB (Код: " + responseCode + "): " + response.getContentText());
          }
          
        Utilities.sleep(25000); 
      }
  
      // 4. ЗАПИСЬ В БАЗУ
      if (newRows.length > 0) {
        updateDataBatch(ss, FUNNEL_SHEET, licenseKey, newRows, "date");
        Logger.log("✅ Успешно синхронизировано строк воронки: " + newRows.length);
      }
  
      const allFunnelData = getRowsAsObjectsByLicense(ss, FUNNEL_SHEET, licenseKey);
      
      // БЕЗОПАСНАЯ СЕРИАЛИЗАЦИЯ ДАТ (Фикс бага google.script.run)
      const safeData = allFunnelData.map(r => {
        r.date = toIsoDate(r.date);
        r.updatedAt = toIsoDate(r.updatedAt);
        return r;
      });
  
      return { success: true, data: safeData || [] }; 
  
    } catch (e) {
      Logger.log("🚨 КАТАСТРОФА apiSyncFunnelData: " + e.message);
      throw new Error("Сбой воронки: " + e.message);
    }
  }
  
  function apiGetTimeSeriesPayload(licenseKey, startDate, endDate) {
    try {
      const ss = getUserDb(licenseKey);
      
      // 1. Чтение сырых массивов из таблиц
      const sales = getRowsAsObjectsByLicense(ss, CONFIG.SHEETS.RAW, licenseKey).filter(r => {
        const d = toIsoDate(r.date_from);
        return d >= startDate && d <= endDate;
      });
      
      const ads = getRowsAsObjectsByLicense(ss, CONFIG.SHEETS.ADS, licenseKey).filter(r => {
        const d = toIsoDate(r.updTime || r.date);
        return d >= startDate && d <= endDate;
      });
      
      let funnel = [];
const sheetFunnel = ss.getSheetByName(FUNNEL_SHEET);
    if (sheetFunnel) {
        funnel = getRowsAsObjectsByLicense(ss, FUNNEL_SHEET, licenseKey).filter(r => {
          const d = toIsoDate(r.date);
          return d >= startDate && d <= endDate;
        });
      }
      
      const art = getRowsByLicense(ss, CONFIG.SHEETS.ARTICLES, licenseKey);
      const costMap = new Map(art.map(r => [String(r[1]), Number(r[4]) || 0]));
  
      // 2. Группировка Time-Series в памяти сервера
      let tsMap = {};
  
      // Агрегация продаж и заказов
      sales.forEach(r => {
        const d = toIsoDate(r.date_from);
        const nm = String(r.nm_id);
        const key = d + "_" + nm;
        
        if (!tsMap[key]) {
          tsMap[key] = { date: d, nmId: nm, sales: 0, margin: 0, adsSum: 0, logSum: 0, storageSum: 0, penaltySum: 0, openCount: 0, cartCount: 0, orderCount: 0, orderSum: 0, cogs: 0, comm: 0 };
        }
  
        let costPerItem = costMap.get(nm) || 0;
        let qty = Number(r.quantity) || 1;
        let s = Number(r.retail_price) || 0;
        let p = Number(r.ppvz_for_pay) || 0;
        let l = Number(r.delivery_rub) || 0;
        let pen = (Number(r.penalty) || 0) + (Number(r.additional_payment) || 0);
        let st = Number(r.storage_fee) || 0;
  
        if (r.doc_type_name === "Продажа") {
          tsMap[key].sales += s;
          tsMap[key].comm += (s - p);
          tsMap[key].cogs += (costPerItem * qty);
        } else if (r.doc_type_name === "Возврат") {
          tsMap[key].sales -= s;
          tsMap[key].comm -= (s - p);
          tsMap[key].cogs -= (costPerItem * qty);
        }
        tsMap[key].logSum += l;
        tsMap[key].penaltySum += pen;
        tsMap[key].storageSum += st;
      });
  
      // Добавление рекламных расходов
      ads.forEach(r => {
        const d = toIsoDate(r.updTime || r.date);
        const nm = String(r.nm_id);
        if (!nm || nm === "undefined" || nm === "") return;
        
        const key = d + "_" + nm;
        if (!tsMap[key]) {
          tsMap[key] = { date: d, nmId: nm, sales: 0, margin: 0, adsSum: 0, logSum: 0, storageSum: 0, penaltySum: 0, openCount: 0, cartCount: 0, orderCount: 0, orderSum: 0, cogs: 0, comm: 0 };
        }
        tsMap[key].adsSum += (Number(r.updSum) || 0);
      });
  
      // Добавление данных воронки
      funnel.forEach(r => {
        const d = toIsoDate(r.date);
        const nm = String(r.nmId);
        const key = d + "_" + nm;
        
        if (!tsMap[key]) {
          tsMap[key] = { date: d, nmId: nm, sales: 0, margin: 0, adsSum: 0, logSum: 0, storageSum: 0, penaltySum: 0, openCount: 0, cartCount: 0, orderCount: 0, orderSum: 0, cogs: 0, comm: 0 };
        }
        tsMap[key].openCount += (Number(r.openCount) || 0);
        tsMap[key].cartCount += (Number(r.cartCount) || 0);
        tsMap[key].orderCount += (Number(r.orderCount) || 0);
        tsMap[key].orderSum += (Number(r.orderSum) || 0);
      });
  
      // 3. Финальный расчет маржи и округление
      const result = Object.values(tsMap).map(row => {
        const tax = row.sales > 0 ? row.sales * CONFIG.TAX_RATE : 0;
        // margin здесь — это чистая прибыль
        row.margin = row.sales - row.comm - row.logSum - row.penaltySum - row.storageSum - row.adsSum - row.cogs - tax;
        
        row.sales = Math.round(row.sales);
        row.margin = Math.round(row.margin);
        row.adsSum = Math.round(row.adsSum);
        row.logSum = Math.round(row.logSum);
        row.storageSum = Math.round(row.storageSum);
        row.cogs = Math.round(row.cogs);
        
        return row;
      }).sort((a, b) => a.date.localeCompare(b.date));
  
      // ГАРАНТИЯ: Возвращаем массив (даже если он пустой), чтобы фронтенд не упал
      return (result && result.length > 0) ? result : [];
  
    } catch (e) {
      console.error("🚨 КРИТИЧЕСКИЙ СБОЙ apiGetTimeSeriesPayload: " + e.message);
      return []; 
    }
  }
  
  
  function testWbFunnelApi() {
    // === 1. НАСТРОЙКИ ТЕСТА ===
    const TEST_LOGIN = "Тестовый 7"; 
    
    Logger.log("=== СТАРТ ИЗОЛИРОВАННОГО ТЕСТА API ВОРОНКИ v3 ===");
    
    // === 2. ПОЛУЧЕНИЕ ТОКЕНА И АРТИКУЛОВ ===
    let API_TOKEN = "";
    let nmIds = [];
    
    try {
      const user = authenticateUser(TEST_LOGIN);
      if (user && user.wb_api_key) {
        API_TOKEN = user.wb_api_key;
        Logger.log("✅ Токен успешно получен из БД.");
      } else {
        Logger.log("❌ Токен не найден в БД для логина: " + TEST_LOGIN);
        return; 
      }
  
      const ss = getUserDb(TEST_LOGIN);
      const allNms = getUniqueNmIdsByLogin(ss, TEST_LOGIN);
      
      if (allNms && allNms.length > 0) {
        nmIds = allNms.slice(0, 2); 
        Logger.log("✅ Найдены артикулы для теста: " + JSON.stringify(nmIds));
      } else {
        Logger.log("⚠️ Артикулы не найдены в DB_Articles. Используем хардкод для теста.");
        nmIds = [12345678]; 
      }
    } catch (err) {
      Logger.log("🚨 Ошибка при чтении БД (тест остановлен): " + err.message);
      return;
    }
  
    // === 3. ФОРМИРОВАНИЕ ЗАПРОСА ===
    const url = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products/history";
    
    // ИСПРАВЛЕНИЕ КРИТИЧЕСКОГО БАГА: "begin" -> "start"
    const payload = {
      "selectedPeriod": {
        "start": "2026-02-15",
        "end": "2026-02-21"
      },
      "nmIds": nmIds,
      "skipDeletedNm": true,
      "aggregationLevel": "day"
    };
    
    Logger.log("PAYLOAD ЗАПРОСА: " + JSON.stringify(payload));
  
    const options = {
      method: "post",
      headers: {
        "Authorization": API_TOKEN,
        "Content-Type": "application/json"
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    };
  
    // === 4. ВЫПОЛНЕНИЕ ЗАПРОСА И ЛОГИРОВАНИЕ ===
    try {
      const response = UrlFetchApp.fetch(url, options);
      const responseCode = response.getResponseCode();
      const responseText = response.getContentText();
      
      Logger.log("HTTP_CODE: " + responseCode);
      Logger.log("WB_RESPONSE: " + responseText.substring(0, 3000)); 
      
    } catch (e) {
      Logger.log("🚨 КРИТИЧЕСКИЙ СБОЙ UrlFetchApp (Сетевая ошибка): " + e.message);
    }
    
    Logger.log("=== КОНЕЦ ТЕСТА ===");
  }
  
  function TRACER_BULLET_FUNNEL(licenseKey) {
    try {
      Logger.log("🟨 [BACKEND_1] Старт TRACER_BULLET. Лицензия: " + licenseKey);
      const ss = getUserDb(licenseKey);
      
      const data = getRowsAsObjectsByLicense(ss, FUNNEL_SHEET, licenseKey);
      Logger.log("🟨 [BACKEND_2] Прочитано строк: " + (data ? data.length : "NULL"));
      
      if (!data || data.length === 0) {
        return JSON.stringify({ status: "EMPTY_SHEET", count: 0 });
      }
      
      return JSON.stringify({ status: "OK", count: data.length, sample: data[0] });
    } catch(e) {
      Logger.log("🟥 [BACKEND_FATAL] " + e.message);
      throw new Error(e.message);
    }
  }
  
  function apiTestSerializationPoC() {
    const nativeDate = new Date(); // Имитируем то, как Google Sheets читает даты из ячеек
    
    return {
      test_name: "Проверка сериализации дат",
      bad_array: [ { id: 1, date: nativeDate, status: "native" } ],
      good_array: [ { id: 2, date: "2026-02-22", status: "string" } ]
    };
  }
  
  
  function apiForceReloadPeriod(licenseKey, dF, dT) {
    try {
      const user = authenticateUser(licenseKey);
      if (!user || !user.wb_api_key) {
        return { success: false, error: "Пользователь или API-ключ не найден" };
      }
      const ss = getUserDb(licenseKey);
      ensureFunnelSheet(ss);
      // 1. Продажи
      const salesUrl = `https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod?dateFrom=${dF}&dateTo=${dT}&period=daily&limit=100000`;
      const salesData = fetchWbWithRrid(salesUrl, user.wb_api_key);
      if (salesData && salesData.length > 0) {
        updateDataBatch(ss, CONFIG.SHEETS.RAW, licenseKey, salesData, "date_from");
        updateArticlesCatalog(ss, salesData, licenseKey);
      }
      
      // 2. Реклама
      const adsUrl = `https://advert-api.wildberries.ru/adv/v1/upd?from=${dF}&to=${dT}`;
      const adData = fetchWb(adsUrl, user.wb_api_key);
      if (adData && Array.isArray(adData) && adData.length > 0) {
        const ids = [...new Set(adData.map(a => a.advertId))];
        const details = getCampaignsDetails(user.wb_api_key, ids);
        let rows = [];
        adData.forEach(ad => {
          let nms = details.get(ad.advertId) || [];
          let sum = Number(ad.updSum) || 0;
          let uTime = ad.updTime || ad.date || dT;
          if (nms.length > 0) {
            let splitSum = sum / nms.length;
            nms.forEach(nm => rows.push({ updTime: uTime, updSum: splitSum, campaignId: ad.advertId, nm_id: String(nm) }));
          } else {
            rows.push({ updTime: uTime, updSum: sum, campaignId: ad.advertId, nm_id: "" });
          }
        });
        updateDataBatch(ss, CONFIG.SHEETS.ADS, licenseKey, rows, "updTime");
      }
      
      // 3. Пересчёт PnL
      updatePnlForPeriod(ss, licenseKey, dF, dT);
      // Воронка грузится отдельным вызовом с клиента после 2026 (избегаем таймаута GAS)
      return { success: true };
    } catch (e) {
      return { success: false, error: e.message || String(e) };
    }
  }
  
  function debugAdsDate() {
    const email = "vitalik-hors@mail.ru";
    const ss = getUserDb(email);
    
    // 1. Какую последнюю дату видит система
    const lastDate = getLastRecordedDateForUser(ss, CONFIG.SHEETS.ADS, email, "updTime");
    console.log("Последняя дата рекламы в БД: " + lastDate);
    console.log("toIsoDate от неё: " + toIsoDate(lastDate));
    
    // 2. Какой период запрашивается
    const today = new Date();
    const endStr = toIsoDate(today);
    const startDate = lastDate 
      ? new Date(lastDate.getTime() + 86400000)
      : new Date(today.getTime() - (30 * 86400000));
    const startStr = toIsoDate(startDate);
    
    console.log("Период который будет запрошен: " + startStr + " → " + endStr);
    
    // 3. Что реально есть в БД по датам
    const adsSheet = ss.getSheetByName(CONFIG.SHEETS.ADS);
    const data = adsSheet.getDataRange().getValues();
    const headers = data[0];
    const updTimeIdx = headers.indexOf("updTime");
    
    // Считаем суммы по датам
    const byDate = {};
    data.slice(1).forEach(row => {
      if (String(row[0]).toLowerCase() !== email.toLowerCase()) return;
      const d = toIsoDate(row[updTimeIdx]);
      if (!byDate[d]) byDate[d] = 0;
      byDate[d] += Number(row[2]) || 0; // col[2] = updSum
    });
    
    // Показываем последние 7 дней
    Object.entries(byDate).sort().slice(-7).forEach(([date, sum]) => {
      console.log("Дата: " + date + " → сумма рекламы: " + sum);
    });
  }