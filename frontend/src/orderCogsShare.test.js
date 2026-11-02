import test from 'node:test';
import assert from 'node:assert/strict';
import { aggregateFunnelDay, buildOrderCogsShareByDate, computePeriodOrderCogsShare } from './orderCogsShare.js';

const row = (date, count, sum, cogs) => ({ date, order_count: count, order_sum: sum, order_cogs: cogs });

test('итог за период — отношение сумм, а не среднее дневных процентов', () => {
  const byDate = buildOrderCogsShareByDate([
    row('2026-01-01', 1, 100, 50),
    row('2026-01-01', 1, 100, 50),
    row('2026-01-02', 1, 800, 80),
    row('2026-01-03', 1, 100, 100),
  ], '2026-01-01', '2026-01-02');
  assert.equal(byDate['2026-01-01'].share, 50);
  assert.equal(byDate['2026-01-02'].share, 10);
  assert.equal(computePeriodOrderCogsShare(byDate, ['2026-01-01', '2026-01-02']), 18);
});

test('при нулевой сумме заказов процент не определён', () => {
  const day = aggregateFunnelDay([row('2026-01-01', 0, 0, 0)]);
  assert.equal(day.valid, true);
  assert.equal(day.share, null);
  assert.equal(computePeriodOrderCogsShare({ '2026-01-01': day }, ['2026-01-01']), null);
});

test('неизвестная себестоимость или количество не дают частичный результат', () => {
  for (const incomplete of [
    row('2026-01-01', 1, 100, null),
    row('2026-01-01', null, null, null),
    row('2026-01-01', null, 0, null),
    row('2026-01-01', 1, null, 25),
  ]) {
    const day = aggregateFunnelDay([row('2026-01-01', 1, 100, 20), incomplete]);
    assert.equal(day.valid, false);
    assert.equal(day.share, null);
    assert.equal(computePeriodOrderCogsShare({ '2026-01-01': day }, ['2026-01-01']), null);
  }
});

test('отсутствующий день не исключается из итога незаметно', () => {
  const day = aggregateFunnelDay([row('2026-01-01', 1, 100, 20)]);
  assert.equal(computePeriodOrderCogsShare({ '2026-01-01': day }, ['2026-01-01', '2026-01-02']), null);
});

test('явный нулевой заказ без стоимости не делает известный день неполным', () => {
  const day = aggregateFunnelDay([
    row('2026-01-01', 1, 100, 20),
    row('2026-01-01', 0, 0, null),
  ]);
  assert.equal(day.valid, true);
  assert.equal(day.share, 20);
});
