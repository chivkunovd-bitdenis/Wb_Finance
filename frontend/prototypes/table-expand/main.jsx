import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import 'bootstrap/dist/css/bootstrap.min.css';
import '../../src/variables.css';
import '../../src/index.css';
import '../../src/design.css';
import './table-expand.css';
import { AuthProvider } from '../../src/AuthContext.jsx';
import App from '../../src/App.jsx';
import ErrorBoundary from '../../src/ErrorBoundary.jsx';
import TableExpansionEnhancement from './TableExpansionEnhancement.jsx';

function installMemoryStorage() {
  const values = new Map();
  const storage = {
    getItem(key) {
      return values.has(String(key)) ? values.get(String(key)) : null;
    },
    setItem(key, value) {
      values.set(String(key), String(value));
    },
    removeItem(key) {
      values.delete(String(key));
    },
    clear() {
      values.clear();
    },
    key(index) {
      return [...values.keys()][index] ?? null;
    },
    get length() {
      return values.size;
    },
  };

  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: storage,
  });
  storage.setItem('wb_finance_token', 'table-expand-prototype-session');
}

installMemoryStorage();

const currentPath = window.location.pathname;
if (currentPath === '/' || currentPath === '/index.html') {
  window.history.replaceState(null, '', `/dashboard${window.location.search}`);
}

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('Элемент #root не найден');

createRoot(rootElement).render(
  <StrictMode>
    <ErrorBoundary>
      <AuthProvider>
        <BrowserRouter>
          <App />
          <TableExpansionEnhancement />
        </BrowserRouter>
      </AuthProvider>
    </ErrorBoundary>
  </StrictMode>,
);
