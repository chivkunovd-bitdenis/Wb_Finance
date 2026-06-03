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
