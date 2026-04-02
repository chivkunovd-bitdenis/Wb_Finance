/* eslint react-refresh/only-export-components: off */
import { createContext, useContext, useState, useCallback } from 'react';
import * as api from './api';
import { lsGet, lsSet, lsRemove } from './safeLocalStorage';

const TOKEN_KEY = 'wb_finance_token';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setTokenState] = useState(() => lsGet(TOKEN_KEY));

  const setToken = useCallback((t) => {
    if (t) lsSet(TOKEN_KEY, t);
    else lsRemove(TOKEN_KEY);
    setTokenState(t);
  }, []);

  const login = useCallback(async (email, password) => {
    const data = await api.login(email, password);
    setToken(data.access_token);
    return data;
  }, [setToken]);

  const register = useCallback(async (email, password, wb_api_key, promo_code) => {
    await api.register(email, password, wb_api_key, promo_code);
    const data = await api.login(email, password);
    setToken(data.access_token);
    return data;
  }, [setToken]);

  const logout = useCallback(() => {
    setToken(null);
  }, [setToken]);

  return (
    <AuthContext.Provider value={{ token, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
