/* eslint react-refresh/only-export-components: off */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import * as api from './api';

const ACTIVE_STORE_KEY = 'wb_finance_active_store_owner_id';

const StoreContext = createContext(null);

function lsGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function lsSet(key, value) {
  try {
    if (value === null || value === undefined || value === '') {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, String(value));
    }
  } catch {
    /* ignore */
  }
}

export function StoreProvider({ children }) {
  const [stores, setStores] = useState([]);
  const [loadingStores, setLoadingStores] = useState(false);
  const [storesError, setStoresError] = useState('');
  const [activeStoreOwnerId, setActiveStoreOwnerIdState] = useState(() => lsGet(ACTIVE_STORE_KEY) || '');

  const refreshStores = useCallback(async () => {
    setLoadingStores(true);
    setStoresError('');
    try {
      const data = await api.getStores();
      const list = Array.isArray(data?.stores) ? data.stores : [];
      setStores(list);

      // Берём выбранный магазин из localStorage в момент ответа, а не из замыкания:
      // параллельное обновление, начатое раньше переключения, не должно откатить выбор.
      const current = lsGet(ACTIVE_STORE_KEY) || '';
      // Ensure active store is accessible; otherwise fallback to own store.
      const activeOk = current && list.some((s) => String(s.owner_user_id) === String(current));
      if (activeOk) {
        setActiveStoreOwnerIdState(current);
      } else {
        // Сотрудник без собственного WB-ключа с одним выданным магазином попадает сразу в него,
        // а не в пустой собственный кабинет.
        const preferred = data?.default_store_owner_user_id ? String(data.default_store_owner_user_id) : '';
        const own = list.find((s) => s.access === 'owner') || list[0];
        const next = preferred && list.some((s) => String(s.owner_user_id) === preferred)
          ? preferred
          : own ? String(own.owner_user_id) : '';
        setActiveStoreOwnerIdState(next);
        lsSet(ACTIVE_STORE_KEY, next);
      }
    } catch (e) {
      setStoresError(e?.message || 'Не удалось загрузить магазины');
    } finally {
      setLoadingStores(false);
    }
  }, []);

  useEffect(() => {
    refreshStores();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setActiveStoreOwnerId = useCallback((ownerId) => {
    const next = ownerId ? String(ownerId) : '';
    setActiveStoreOwnerIdState(next);
    lsSet(ACTIVE_STORE_KEY, next);
  }, []);

  const activeStore = useMemo(
    () => stores.find((s) => String(s.owner_user_id) === String(activeStoreOwnerId)) || null,
    [stores, activeStoreOwnerId],
  );
  const isOwnerStore = activeStore ? activeStore.access === 'owner' : true;
  const activeStoreLabel = activeStore ? activeStore.owner_email : '';

  return (
    <StoreContext.Provider
      value={{
        stores,
        loadingStores,
        storesError,
        refreshStores,
        activeStoreOwnerId,
        setActiveStoreOwnerId,
        activeStore,
        activeStoreLabel,
        isOwnerStore,
      }}
    >
      {children}
    </StoreContext.Provider>
  );
}

export function useStore() {
  const ctx = useContext(StoreContext);
  return (
    ctx || {
      stores: [],
      loadingStores: false,
      storesError: '',
      refreshStores: async () => {},
      activeStoreOwnerId: '',
      setActiveStoreOwnerId: () => {},
      activeStore: null,
      activeStoreLabel: '',
      isOwnerStore: true,
    }
  );
}

