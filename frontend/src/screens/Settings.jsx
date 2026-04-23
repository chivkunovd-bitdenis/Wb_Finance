import { useCallback, useEffect, useMemo, useState } from 'react';
import * as api from '../api';
import { useStore } from '../StoreContext';

export default function Settings() {
  const { refreshStores } = useStore();
  const [email, setEmail] = useState('');
  const [lockedEmail, setLockedEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const isGranted = Boolean(lockedEmail);
  const value = isGranted ? lockedEmail : email;

  const loadExisting = useCallback(async () => {
    setError('');
    try {
      const data = await api.getOutgoingStoreGrants();
      const grants = Array.isArray(data?.grants) ? data.grants : [];
      const active = grants.find((g) => g.status === 'active' && !g.revoked_at) || null;
      if (active?.viewer_email) {
        setLockedEmail(String(active.viewer_email));
      } else {
        setLockedEmail('');
      }
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить настройки доступа');
    }
  }, []);

  useEffect(() => {
    loadExisting();
  }, [loadExisting]);

  const helperText = useMemo(() => {
    if (isGranted) return 'Доступ выдан. Можно отключить в любой момент.';
    return 'Введите e-mail селлера (существующего в системе), которому вы хотите выдать доступ к своему магазину.';
  }, [isGranted]);

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 12 }}>Настройки</div>

      <div
        style={{
          padding: 14,
          border: '1px solid rgba(0,0,0,0.12)',
          borderRadius: 12,
          background: 'white',
        }}
      >
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Выдать доступ к магазину</div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 10 }}>{helperText}</div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            value={value}
            disabled={loading || isGranted}
            placeholder="seller@example.com"
            onChange={(e) => setEmail(e.target.value)}
            style={{ minWidth: 260, flex: '1 1 260px' }}
          />

          {!isGranted ? (
            <button
              className="btn-primary"
              disabled={loading || !String(email).trim()}
              onClick={async () => {
                setLoading(true);
                setError('');
                try {
                  const target = String(email).trim();
                  await api.grantStoreAccess(target);
                  setLockedEmail(target);
                  setEmail('');
                  await refreshStores();
                } catch (e) {
                  setError(e?.message || 'Не удалось выдать доступ');
                } finally {
                  setLoading(false);
                }
              }}
            >
              {loading ? '...' : 'Выдать доступ'}
            </button>
          ) : (
            <button
              className="btn-danger"
              disabled={loading}
              onClick={async () => {
                const ok = window.confirm('Отключить доступ селлеру к вашему магазину?');
                if (!ok) return;
                setLoading(true);
                setError('');
                try {
                  await api.revokeStoreAccess(String(lockedEmail));
                  setLockedEmail('');
                  await refreshStores();
                } catch (e) {
                  setError(e?.message || 'Не удалось отключить доступ');
                } finally {
                  setLoading(false);
                }
              }}
            >
              {loading ? '...' : 'Отключить доступ'}
            </button>
          )}
        </div>

        {isGranted && (
          <div
            style={{
              marginTop: 10,
              padding: '10px 12px',
              borderRadius: 10,
              background: '#e8f4ec',
              color: '#155724',
              border: '1px solid #c3e6cb',
              fontSize: 13,
            }}
          >
            Доступ активен для: <strong>{lockedEmail}</strong>
          </div>
        )}

        {error && (
          <div style={{ marginTop: 10, color: 'var(--red)', fontSize: 13 }}>
            {error}
          </div>
        )}
      </div>
    </div>
  );
}

