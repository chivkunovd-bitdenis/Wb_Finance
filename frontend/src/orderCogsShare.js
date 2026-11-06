/**
 * «Доля себеса заказов»: сумма order_cogs / сумма order_sum × 100 по строкам воронки за день.
 * Если у товара с заказами неизвестны order_cogs, order_count или order_sum — день и период без частичного итога.
 */

/** @returns {{ valid: boolean, sumCogs: number, sumOrderSum: number, share: number | null }} */
export function aggregateFunnelDay(rows) {
  let sumCogs = 0;
  let sumOrderSum = 0;

  for (const r of rows || []) {
    const orderCount = r.order_count;
    const orderSum = r.order_sum;
    const orderCogs = r.order_cogs;

    if (orderCount == null || !Number.isFinite(Number(orderCount)) || Number(orderCount) < 0) {
      return { valid: false, sumCogs: 0, sumOrderSum: 0, share: null };
    }
    if (Number(orderCount) === 0) {
      if (orderSum == null || Number(orderSum) === 0) continue;
      return { valid: false, sumCogs: 0, sumOrderSum: 0, share: null };
    }
    if (orderSum == null || orderCogs == null) {
      return { valid: false, sumCogs: 0, sumOrderSum: 0, share: null };
    }

    const oc = Number(orderCount);
    const os = Number(orderSum);
    const ocg = Number(orderCogs);
    if (!Number.isFinite(oc) || !Number.isFinite(os) || !Number.isFinite(ocg) || os < 0 || ocg < 0) {
      return { valid: false, sumCogs: 0, sumOrderSum: 0, share: null };
    }

    sumCogs += ocg;
    sumOrderSum += os;
  }

  if (sumOrderSum <= 0) {
    return { valid: true, sumCogs, sumOrderSum, share: null };
  }

  return { valid: true, sumCogs, sumOrderSum, share: (sumCogs / sumOrderSum) * 100 };
}

/**
 * @param {Record<string, ReturnType<typeof aggregateFunnelDay>>} byDate
 * @param {string[]} datesInPeriod
 * @returns {number | null}
 */
export function computePeriodOrderCogsShare(byDate, datesInPeriod) {
  let totalCogs = 0;
  let totalOrderSum = 0;

  for (const d of datesInPeriod || []) {
    const agg = byDate?.[d];
    if (!agg) return null;
    if (!agg.valid) return null;
    totalCogs += agg.sumCogs;
    totalOrderSum += agg.sumOrderSum;
  }

  if (totalOrderSum <= 0) return null;
  return (totalCogs / totalOrderSum) * 100;
}

/**
 * «Себес заказов» за день в рублях. Неизвестен, если день не загружен или у товара с заказами нет себестоимости.
 * @param {ReturnType<typeof aggregateFunnelDay> | undefined} agg
 * @returns {number | null}
 */
export function dayOrderCogsSum(agg) {
  return agg?.valid ? agg.sumCogs : null;
}

/**
 * Итог «Себес заказов» в рублях. Ещё не загруженный день пропускается, как и в итоге «Заказы ₽» рядом.
 * День с неизвестной себестоимостью даёт прочерк: заниженная сумма хуже отсутствующей.
 * @param {Record<string, ReturnType<typeof aggregateFunnelDay>>} byDate
 * @param {string[]} datesInPeriod
 * @returns {number | null}
 */
export function computePeriodOrderCogsSum(byDate, datesInPeriod) {
  let total = 0;
  let anyDayKnown = false;

  for (const d of datesInPeriod || []) {
    const agg = byDate?.[d];
    if (!agg) continue;
    if (!agg.valid) return null;
    total += agg.sumCogs;
    anyDayKnown = true;
  }

  return anyDayKnown ? total : null;
}

/** @param {Array<{ date?: string }>} funnelRows */
export function buildOrderCogsShareByDate(funnelRows, dateFrom, dateTo) {
  const grouped = {};
  for (const r of funnelRows || []) {
    const d = r.date;
    if (!d) continue;
    if (dateFrom && d < dateFrom) continue;
    if (dateTo && d > dateTo) continue;
    if (!grouped[d]) grouped[d] = [];
    grouped[d].push(r);
  }

  const byDate = {};
  for (const [d, rows] of Object.entries(grouped)) {
    byDate[d] = aggregateFunnelDay(rows);
  }
  return byDate;
}

export const ORDER_COGS_SHARE_HEADER_TITLE =
  'Сумма себестоимости заказов ÷ сумма заказов × 100% за день. Себестоимость единицы — актуальная из карточки товара (количество заказов × себестоимость).';

export const ORDER_COGS_SUM_HEADER_TITLE =
  'Сумма себестоимости заказов за день, ₽: количество заказов × себестоимость. Себестоимость единицы — актуальная из карточки товара. Итог считается по загруженным дням.';
