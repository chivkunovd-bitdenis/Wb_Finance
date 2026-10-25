/*
 * Value rules shared by the WB screens when they show Ozon or combined data.
 *
 * WB keeps its legacy presentation: a missing number is zero. Ozon and the combined view
 * never invent zeros: an unknown value is null, sums with an unknown part are unknown, and
 * percentages of unknown values are unknown. Callers pass `unknownAsNull` (true for non-WB).
 */

/** A number for display or arithmetic. With unknownAsNull, a missing or non-numeric value stays null. */
export function toNumber(value, unknownAsNull) {
  if (value === null || value === undefined || value === '') return unknownAsNull ? null : 0;
  const n = Number(value);
  if (Number.isFinite(n)) return n;
  return unknownAsNull ? null : 0;
}

/** Sum that stays unknown as soon as one part is unknown. */
export function plusValue(a, b) {
  if (a === null || a === undefined || b === null || b === undefined) return null;
  return a + b;
}

/**
 * Sum over the known parts only: an unknown part is skipped, not zeroed and not allowed to blank the total.
 * The total is unknown only when no part is known at all (null accumulator and null value).
 */
export function addKnown(total, value) {
  if (value === null || value === undefined) return total;
  if (total === null || total === undefined) return value;
  return total + value;
}

/** Sum of several values, left to right; unknown as soon as one is unknown. */
export function sumKnown(values) {
  return values.reduce((acc, value) => plusValue(acc, value), 0);
}

/** Share of a base in percent: 0 when the base is not positive (WB convention), unknown when either part is unknown. */
export function percentOf(part, base) {
  if (part === null || part === undefined || base === null || base === undefined) return null;
  return base > 0 ? (part / base) * 100 : 0;
}

/** Whether a row's value is known. */
export function isKnown(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value));
}

/** Dash for unknown values, the number with the given fraction digits otherwise. */
export function formatFixedOrDash(value, digits) {
  if (!isKnown(value)) return '—';
  return Number(value).toFixed(digits);
}
