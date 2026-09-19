import { useEffect, useRef } from 'react';
import { useStore } from './StoreContext';

/** Текущий owner id магазина (для привязки запросов и кэша). */
export function useActiveStoreId() {
  const { activeStoreOwnerId } = useStore();
  return activeStoreOwnerId || 'self';
}

/**
 * Сбрасывает локальное состояние экрана при смене магазина.
 * Предотвращает показ артикулов/SKU предыдущего магазина до прихода новых данных.
 */
export function useResetOnStoreChange(storeId, resetFn) {
  const prevRef = useRef(storeId);
  useEffect(() => {
    if (String(prevRef.current) === String(storeId)) return;
    prevRef.current = storeId;
    resetFn();
  }, [storeId, resetFn]);
}

/** Ответ устарел, если пользователь уже переключил магазин. */
export function isStaleStoreResponse(requestStoreId, currentStoreId) {
  return String(requestStoreId) !== String(currentStoreId);
}

export function isFinanceMissingSyncActive(sync) {
  if (!sync) return false;
  if (sync.status === 'queued' || sync.status === 'running') return true;
  if (sync.status === 'idle' && sync.next_run_at) {
    const ts = new Date(sync.next_run_at).getTime();
    return !Number.isNaN(ts) && ts > Date.now();
  }
  return false;
}

export function isFunnelTailSyncActive(tail) {
  if (!tail?.pending) return false;
  return ['queued', 'scheduled', 'running', 'cooldown'].includes(tail.status);
}

/**
 * «Отпечаток» прогресса фоновых досинхронизаций из /dashboard/state.
 * Меняется только когда оркестратор реально продвинулся (закрыл шаг, взял новый диапазон,
 * появились свежие даты), а не на каждом опросе и не на каждом 429/cooldown от WB.
 * Экраны перечитывают данные только при смене отпечатка — иначе поля ввода
 * (налоговая ставка, себестоимость, планы) сбрасываются каждые 5 секунд.
 */
export function syncProgressSignature(state) {
  if (!state) return '';
  const fin = state.finance_missing_sync || {};
  const tail = state.funnel_tail_sync || {};
  return [
    state.max_date || '',
    state.has_funnel ? 1 : 0,
    fin.status || '',
    fin.date_from || '',
    fin.date_to || '',
    tail.pending ? 1 : 0,
    tail.last_step || '',
  ].join('|');
}
