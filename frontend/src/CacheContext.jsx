/* eslint react-refresh/only-export-components: off */
import { createContext, useContext, useState, useCallback } from 'react';
import { useStore } from './StoreContext';

const CACHE_KEY = 'wb_finance_data_cache';
const HOT_STORES_LIMIT = 4;

const CacheContext = createContext(null);

export function CacheProvider({ children }) {
  const { activeStoreOwnerId } = useStore();
  const [cache, setCacheState] = useState(() => {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });

  const updateCache = useCallback((slice, data) => {
    setCacheState((prev) => {
      const storeId = activeStoreOwnerId || 'self';
      const prevObj = prev && typeof prev === 'object' ? prev : {};
      const prevByStore = prevObj.byStore && typeof prevObj.byStore === 'object' ? prevObj.byStore : {};
      const prevLru = Array.isArray(prevObj.lruStores) ? prevObj.lruStores : [];

      const nextStoreCache = { ...(prevByStore[storeId] || {}), [slice]: data };
      const nextByStore = { ...prevByStore, [storeId]: nextStoreCache };

      // LRU update
      const without = prevLru.filter((x) => String(x) !== String(storeId));
      const nextLru = [storeId, ...without];
      const evicted = nextLru.slice(HOT_STORES_LIMIT);
      const finalLru = nextLru.slice(0, HOT_STORES_LIMIT);
      for (const sid of evicted) {
        delete nextByStore[sid];
      }

      const next = { byStore: nextByStore, lruStores: finalLru };
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(next));
      } catch (e) {
        console.warn('Cache write failed', e);
      }
      return next;
    });
  }, [activeStoreOwnerId]);

  const clearCache = useCallback(() => {
    try {
      localStorage.removeItem(CACHE_KEY);
    } catch (e) {
      void e; // keep block non-empty for eslint
    }
    setCacheState(null);
  }, []);

  const storeScopedCache = (() => {
    const storeId = activeStoreOwnerId || 'self';
    const obj = cache && typeof cache === 'object' ? cache : null;
    const byStore = obj && obj.byStore && typeof obj.byStore === 'object' ? obj.byStore : null;
    const sc = byStore && byStore[storeId] && typeof byStore[storeId] === 'object' ? byStore[storeId] : null;
    return sc || null;
  })();

  return (
    <CacheContext.Provider value={{ cache: storeScopedCache, updateCache, clearCache }}>
      {children}
    </CacheContext.Provider>
  );
}

export function useCache() {
  const ctx = useContext(CacheContext);
  return ctx || { cache: null, updateCache: () => {}, clearCache: () => {} };
}
