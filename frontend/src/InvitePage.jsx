import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import * as api from './api';
import { useAuth } from './AuthContext';
import { useStore } from './StoreContext';

const ERROR_STYLE = { color: '#ee5253', marginBottom: 12, fontSize: 14 };
const MUTED_STYLE = { color: '#6c757d', fontSize: '0.875rem', marginBottom: '1rem' };

/**
 * Страница приглашения сотрудника: /invite/<token>.
 * Открывается и без входа (регистрация или вход на email из приглашения),
 * и после входа (принять приглашение, если email совпадает).
 */
export default function InvitePage({ token }) {
  const { token: authToken } = useAuth();
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    let cancelled = false;
    api
      .lookupStoreInvite(token)
      .then((data) => {
        if (!cancelled) setInfo(data);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e?.message || 'Не удалось проверить приглашение');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  let body;
  if (loading) {
    body = <p style={MUTED_STYLE}>Проверяем приглашение…</p>;
  } else if (loadError || !info) {
    body = <InviteUnavailable message={loadError || 'Приглашение не найдено'} loggedIn={Boolean(authToken)} />;
  } else if (authToken) {
    body = <MemberInviteView token={token} info={info} />;
  } else {
    body = <GuestInviteView token={token} info={info} />;
  }

  return (
    <div className="login-wrapper">
      <div className="login-card">
        <h3 className="login-title">ПРИГЛАШЕНИЕ В КАБИНЕТ</h3>
        {body}
      </div>
    </div>
  );
}

function InviteUnavailable({ message, loggedIn }) {
  const navigate = useNavigate();
  return (
    <>
      <div style={ERROR_STYLE}>{message}</div>
      {loggedIn && (
        <button type="button" className="btn btn-primary login-btn" onClick={() => navigate('/dashboard', { replace: true })}>
          Перейти в мой кабинет
        </button>
      )}
    </>
  );
}

function statusText(status, ownerEmail) {
  if (status === 'expired') return 'Срок приглашения истёк. Попросите владельца кабинета отправить новое приглашение.';
  if (status === 'revoked') return 'Приглашение отозвано владельцем кабинета. Попросите владельца отправить новое.';
  if (status === 'accepted') return `Приглашение в кабинет ${ownerEmail} уже принято.`;
  return '';
}

/** Не вошедший пользователь: регистрация или вход на email из приглашения. */
function GuestInviteView({ token, info }) {
  const { register, login } = useAuth();
  const isPending = info.status === 'pending';
  const isAccepted = info.status === 'accepted';
  const [mode, setMode] = useState(isAccepted ? 'login' : 'register');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    if (!password.trim()) {
      setError('Введите пароль');
      return;
    }
    setBusy(true);
    try {
      if (mode === 'register') {
        // Успешный вход меняет token в AuthContext: страница перемонтируется уже как вошедшая.
        await register(info.email, password, null, null, token);
      } else {
        await login(info.email, password);
      }
    } catch (err) {
      setError(err?.message || 'Ошибка');
      setBusy(false);
    }
  }

  function switchMode(next) {
    setMode(next);
    setError('');
    setPassword('');
  }

  if (!isPending && !isAccepted) {
    return (
      <>
        <p style={MUTED_STYLE}>{statusText(info.status, info.owner_email)}</p>
        <a href="/" style={{ color: 'var(--primary)', fontWeight: 700, fontSize: '0.875rem' }}>
          Перейти ко входу
        </a>
      </>
    );
  }

  return (
    <>
      <p style={MUTED_STYLE}>
        {isPending ? (
          <>
            Вас пригласили в кабинет <strong>{info.owner_email}</strong>.{' '}
            {mode === 'register' ? 'Создайте аккаунт на указанном email.' : 'Войдите в свой аккаунт, чтобы принять приглашение.'}
          </>
        ) : (
          statusText(info.status, info.owner_email)
        )}
      </p>

      <form onSubmit={handleSubmit}>
        <input type="email" className="login-input" value={info.email} readOnly aria-label="Email приглашения" />
        <input
          type="password"
          className="login-input"
          placeholder="Пароль"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
        />
        {error && <div style={ERROR_STYLE}>{error}</div>}
        <button type="submit" className="btn btn-primary login-btn mt-2" disabled={busy}>
          {busy ? '...' : mode === 'register' ? 'Зарегистрироваться' : 'Войти'}
        </button>
      </form>

      {isPending && (
        <div style={{ marginTop: '1rem', fontSize: '0.875rem' }}>
          {mode === 'register' ? (
            <span
              style={{ color: 'var(--primary)', fontWeight: 700, cursor: 'pointer' }}
              onClick={() => switchMode('login')}
            >
              У меня уже есть аккаунт
            </span>
          ) : (
            <span
              style={{ color: 'var(--primary)', fontWeight: 700, cursor: 'pointer' }}
              onClick={() => switchMode('register')}
            >
              Создать новый аккаунт по приглашению
            </span>
          )}
        </div>
      )}
    </>
  );
}

/** Вошедший пользователь: принять приглашение, если email совпадает с приглашением. */
function MemberInviteView({ token, info }) {
  const navigate = useNavigate();
  const { logout } = useAuth();
  const { stores, storesError, refreshStores, setActiveStoreOwnerId } = useStore();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const openedRef = useRef(false);

  // Свой email берём из списка магазинов: там всегда есть запись access=owner с email самого пользователя.
  const ownEntry = stores.find((s) => s.access === 'owner') || null;
  const ownEmail = ownEntry ? String(ownEntry.owner_email) : '';
  const emailMatches = Boolean(ownEmail) && ownEmail.toLowerCase() === String(info.email).toLowerCase();
  const grantedStore =
    stores.find(
      (s) =>
        s.access === 'granted' &&
        String(s.owner_email).toLowerCase() === String(info.owner_email).toLowerCase(),
    ) || null;

  // Приглашение уже принято этим аккаунтом (например, сразу после регистрации): открываем кабинет.
  useEffect(() => {
    if (!emailMatches || info.status !== 'accepted' || !grantedStore || openedRef.current) return;
    openedRef.current = true;
    setActiveStoreOwnerId(grantedStore.owner_user_id);
    navigate('/dashboard', { replace: true });
  }, [emailMatches, info.status, grantedStore, setActiveStoreOwnerId, navigate]);

  async function handleAccept() {
    setBusy(true);
    setError('');
    try {
      const res = await api.acceptStoreInvite(token);
      // refreshStores читает активный магазин из замыкания, поэтому активный ставим уже после обновления.
      await refreshStores();
      setActiveStoreOwnerId(res.owner_user_id);
      navigate('/dashboard', { replace: true });
    } catch (e) {
      setError(e?.message || 'Не удалось принять приглашение');
      setBusy(false);
    }
  }

  if (!ownEmail) {
    return <p style={MUTED_STYLE}>{storesError || 'Загружаем ваш аккаунт…'}</p>;
  }

  if (!emailMatches) {
    return (
      <>
        <p style={MUTED_STYLE}>
          Вы вошли как <strong>{ownEmail}</strong>, а приглашение выписано на <strong>{info.email}</strong>.
          Выйдите и войдите под нужным email, чтобы принять приглашение.
        </p>
        <button type="button" className="btn btn-primary login-btn" onClick={logout}>
          Выйти
        </button>
      </>
    );
  }

  if (info.status === 'pending') {
    return (
      <>
        <p style={MUTED_STYLE}>
          Вас пригласили в кабинет <strong>{info.owner_email}</strong>.
        </p>
        {error && <div style={ERROR_STYLE}>{error}</div>}
        <button type="button" className="btn btn-primary login-btn" disabled={busy} onClick={handleAccept}>
          {busy ? '...' : 'Принять приглашение'}
        </button>
      </>
    );
  }

  if (info.status === 'accepted' && grantedStore) {
    return <p style={MUTED_STYLE}>Открываем кабинет {info.owner_email}…</p>;
  }

  return (
    <>
      <p style={MUTED_STYLE}>
        {info.status === 'accepted'
          ? `Доступ к кабинету ${info.owner_email} сейчас не выдан. Попросите владельца отправить новое приглашение.`
          : statusText(info.status, info.owner_email)}
      </p>
      <button
        type="button"
        className="btn btn-primary login-btn"
        onClick={() => navigate('/dashboard', { replace: true })}
      >
        Перейти в мой кабинет
      </button>
    </>
  );
}
