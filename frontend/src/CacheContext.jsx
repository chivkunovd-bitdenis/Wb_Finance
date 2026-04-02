/* eslint react-refresh/only-export-components: off */
import { createContext, useContext, useState, useCallback } from 'react';

const CACHE_KEY = 'wb_finance_data_cache';

const CacheContext = createContext(null);

export function CacheProvider({ children }) {
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
      const next = { ...(prev || {}), [slice]: data };
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(next));
      } catch (e) {
        console.warn('Cache write failed', e);
      }
      return next;
    });
  }, []);

  const clearCache = useCallback(() => {
    try {
      localStorage.removeItem(CACHE_KEY);
    } catch (e) {
      void e; // keep block non-empty for eslint
    }
    setCacheState(null);
  }, []);

  return (
    <CacheContext.Provider value={{ cache, updateCache, clearCache }}>
      {children}
    </CacheContext.Provider>
  );
}

export function useCache() {
  const ctx = useContext(CacheContext);
  return ctx || { cache: null, updateCache: () => {}, clearCache: () => {} };
}
