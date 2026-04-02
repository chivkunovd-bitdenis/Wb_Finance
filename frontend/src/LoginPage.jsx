import { useMemo, useState } from 'react';
import { useAuth } from './AuthContext';

export default function LoginPage() {
  const { login, register } = useAuth();
  const openRegister = useMemo(() => {
    try {
      return new URLSearchParams(window.location.search).get('register') === '1';
    } catch {
      return false;
    }
  }, []);
  const [isLoginMode, setIsLoginMode] = useState(!openRegister);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [wbKey, setWbKey] = useState('');
  const [promoCode, setPromoCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    if (!email.trim() || !password.trim()) {
      setError('Заполните логин и пароль');
      return;
    }
    if (!isLoginMode && !wbKey.trim() && !promoCode.trim()) {
      setError('Укажите API-ключ WB или промокод для регистрации');
      return;
    }
    setLoading(true);
    try {
      if (isLoginMode) {
        await login(email.trim(), password);
      } else {
        await register(
          email.trim(),
          password,
          wbKey.trim() || null,
          promoCode.trim() || null,
        );
      }
    } catch (err) {
      setError(err.message || 'Ошибка');
    } finally {
      setLoading(false);
    }
  }

  function switchMode() {
    setIsLoginMode(!isLoginMode);
    setError('');
    setPromoCode('');
  }

  return (
    <div className="login-wrapper">
      <div className="login-card">
        <h3 className="login-title">{isLoginMode ? 'ВХОД В СИСТЕМУ' : 'РЕГИСТРАЦИЯ'}</h3>
        <p className="text-muted small mb-4" style={{ color: '#6c757d', fontSize: '0.875rem', marginBottom: '1rem' }}>
          {isLoginMode ? 'Введите данные для доступа к дашбордам' : 'Создайте аккаунт и подключите свой магазин'}
        </p>

        <form onSubmit={handleSubmit}>
          <input
            type="email"
            className="login-input"
            placeholder="Логин (Email)"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
          />
          <input
            type="password"
            className="login-input"
            placeholder="Пароль"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={isLoginMode ? 'current-password' : 'new-password'}
          />
          {!isLoginMode && (
            <>
              <input
                type="text"
                className="login-input"
                placeholder="Ключ API статистики WB (только чтение)"
                value={wbKey}
                onChange={(e) => setWbKey(e.target.value)}
              />

              {/* Разделитель */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '4px 0' }}>
                <div style={{ flex: 1, height: '0.5px', background: '#e5e7eb' }} />
                <span style={{ fontSize: 11, color: '#9ca3af', whiteSpace: 'nowrap' }}>или есть промокод</span>
                <div style={{ flex: 1, height: '0.5px', background: '#e5e7eb' }} />
              </div>

              <input
                type="text"
                className="login-input"
                placeholder="Промокод (XXXX-XXXX-XXXX)"
                value={promoCode}
                onChange={(e) => setPromoCode(e.target.value.toUpperCase())}
                style={{ letterSpacing: '0.05em' }}
              />
            </>
          )}

          {error && <div style={{ color: '#ee5253', marginBottom: 12, fontSize: 14 }}>{error}</div>}

          <button type="submit" className="btn btn-primary login-btn mt-2" disabled={loading}>
            {loading ? '...' : isLoginMode ? 'Войти' : 'Создать аккаунт'}
          </button>
        </form>

        <div style={{ marginTop: '1rem', fontSize: '0.875rem' }}>
          <span style={{ color: '#6c757d' }}>{isLoginMode ? 'Нет аккаунта?' : 'Уже есть аккаунт?'}</span>{' '}
          <span
            style={{ color: 'var(--primary)', fontWeight: 700, cursor: 'pointer' }}
            onClick={switchMode}
          >
            {isLoginMode ? 'Зарегистрироваться' : 'Войти'}
          </span>
        </div>
      </div>
    </div>
  );
}
