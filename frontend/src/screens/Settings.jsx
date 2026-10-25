/* eslint react-hooks/set-state-in-effect: off */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api';
import { useStore } from '../StoreContext';

export default function Settings() {
  const { activeStore, activeStoreOwnerId, isOwnerStore } = useStore();
  const canManageOzon = Boolean(activeStore && isOwnerStore);
  // Сотрудников приглашает только владелец магазина: в чужом кабинете блок скрыт.
  const canManageTeam = Boolean(activeStore && isOwnerStore);

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 12 }}>Настройки</div>

      <OzonConnectionSettings key={activeStoreOwnerId || 'self'} activeStore={activeStore} activeStoreOwnerId={activeStoreOwnerId} canManage={canManageOzon} />
      <OzonFinancialSettings key={`finance:${activeStoreOwnerId || 'self'}`} activeStoreOwnerId={activeStoreOwnerId} canManage={canManageOzon} />
      <OzonManualSync key={`sync:${activeStoreOwnerId || 'self'}`} canManage={canManageOzon} />

      <TeamAccessSettings canManage={canManageTeam} />
    </div>
  );
}

const teamRowStyle = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  gap: 10,
  padding: '8px 0',
  borderBottom: '1px solid rgba(0,0,0,0.06)',
  fontSize: 13,
};

function formatInviteDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('ru');
}

function TeamAccessSettings({ canManage }) {
  const [managers, setManagers] = useState([]);
  const [invites, setInvites] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [created, setCreated] = useState(null);
  const [copied, setCopied] = useState(false);

  const reload = useCallback(async () => {
    try {
      const data = await api.getStoreTeam();
      setManagers(Array.isArray(data?.managers) ? data.managers : []);
      setInvites(Array.isArray(data?.invites) ? data.invites : []);
      setLoadError('');
    } catch (e) {
      setLoadError(e?.message || 'Не удалось загрузить список сотрудников');
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (canManage) reload();
  }, [canManage, reload]);

  if (!canManage) return null;

  async function handleInvite() {
    const target = String(email).trim();
    if (!target) return;
    setBusy(true);
    setError('');
    setCopied(false);
    try {
      const data = await api.createStoreInvite(target);
      setCreated({
        email: String(data.email),
        link: `${window.location.origin}${data.invite_url_path}`,
        expiresAt: data.expires_at,
      });
      setEmail('');
      await reload();
    } catch (e) {
      setError(e?.message || 'Не удалось создать приглашение');
    } finally {
      setBusy(false);
    }
  }

  async function handleCopy() {
    if (!created) return;
    setError('');
    try {
      await navigator.clipboard.writeText(created.link);
      setCopied(true);
    } catch {
      setError('Не удалось скопировать автоматически. Выделите ссылку и скопируйте вручную.');
    }
  }

  async function handleRevokeManager(managerEmail) {
    const ok = window.confirm('Отключить доступ сотруднику к вашему кабинету?');
    if (!ok) return;
    setBusy(true);
    setError('');
    try {
      await api.revokeStoreAccess(String(managerEmail));
      await reload();
    } catch (e) {
      setError(e?.message || 'Не удалось отключить доступ');
    } finally {
      setBusy(false);
    }
  }

  async function handleRevokeInvite(inviteId) {
    setBusy(true);
    setError('');
    try {
      await api.revokeStoreInvite(inviteId);
      await reload();
    } catch (e) {
      setError(e?.message || 'Не удалось отозвать приглашение');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        marginTop: 12,
        padding: 14,
        border: '1px solid rgba(0,0,0,0.12)',
        borderRadius: 12,
        background: 'white',
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: 6 }}>Сотрудники</div>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 10 }}>
        Пригласите сотрудника по email. Он зарегистрируется по ссылке и увидит ваш кабинет.
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          type="email"
          value={email}
          disabled={busy}
          placeholder="employee@example.com"
          onChange={(e) => setEmail(e.target.value)}
          style={{ minWidth: 260, flex: '1 1 260px' }}
        />
        <button className="btn-primary" disabled={busy || !String(email).trim()} onClick={handleInvite}>
          {busy ? '...' : 'Пригласить'}
        </button>
      </div>

      {created && (
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
          <div>
            Приглашение для <strong>{created.email}</strong> готово. Действует до {formatInviteDate(created.expiresAt)}.
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8, flexWrap: 'wrap' }}>
            <input
              type="text"
              readOnly
              value={created.link}
              onFocus={(e) => e.target.select()}
              style={{ flex: '1 1 260px', minWidth: 200 }}
            />
            <button className="btn-primary" onClick={handleCopy}>
              {copied ? 'Скопировано' : 'Скопировать'}
            </button>
          </div>
          <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-tertiary)' }}>
            Отправьте ссылку сотруднику — по ней он зарегистрируется и увидит ваш кабинет
          </div>
        </div>
      )}

      <div style={{ marginTop: 16, fontWeight: 600, fontSize: 13 }}>Сотрудники с доступом</div>
      {!loaded ? (
        <div style={{ fontSize: 13, color: 'var(--text-tertiary)', marginTop: 6 }}>Загрузка…</div>
      ) : managers.length === 0 ? (
        <div style={{ fontSize: 13, color: 'var(--text-tertiary)', marginTop: 6 }}>Пока никого нет</div>
      ) : (
        managers.map((m) => (
          <div key={m.viewer_user_id} style={teamRowStyle}>
            <span>{m.email}</span>
            <button className="btn-danger" disabled={busy} onClick={() => handleRevokeManager(m.email)}>
              Отключить
            </button>
          </div>
        ))
      )}

      <div style={{ marginTop: 16, fontWeight: 600, fontSize: 13 }}>Приглашения</div>
      {!loaded ? null : invites.length === 0 ? (
        <div style={{ fontSize: 13, color: 'var(--text-tertiary)', marginTop: 6 }}>Нет активных приглашений</div>
      ) : (
        invites.map((i) => (
          <div key={i.id} style={teamRowStyle}>
            <span>
              {i.email}{' '}
              <span style={{ color: i.status === 'expired' ? 'var(--red)' : 'var(--text-tertiary)' }}>
                {i.status === 'expired' ? '· срок истёк' : `· действует до ${formatInviteDate(i.expires_at)}`}
              </span>
            </span>
            <button className="btn-danger" disabled={busy} onClick={() => handleRevokeInvite(i.id)}>
              Отозвать
            </button>
          </div>
        ))
      )}

      {loadError && (
        <div style={{ marginTop: 10, color: 'var(--red)', fontSize: 13 }}>
          {loadError}
        </div>
      )}
      {error && (
        <div style={{ marginTop: 10, color: 'var(--red)', fontSize: 13 }}>
          {error}
        </div>
      )}
    </div>
  );
}

function getOzonConnectionStatus(connection, { loading = false, saving = false, loadError = '' } = {}) {
  const status = String(connection?.status || '').toLowerCase();
  if (loading || saving || ['syncing', 'queued', 'pending', 'running'].includes(status)) {
    return { label: 'Проверяем', className: 'tag-marketplace-syncing' };
  }
  if (loadError || ['error', 'needs_reauth', 'failed'].includes(status)) {
    return { label: 'Ошибка', className: 'tag-marketplace-error' };
  }
  if (status === 'ready') {
    return { label: 'Подключён', className: 'tag-marketplace-connected' };
  }
  if (status === 'configured' || connection?.configured === true) {
    return { label: 'Данные сохранены', className: 'tag-gray' };
  }
  return { label: 'Не подключён', className: 'tag-gray' };
}

function formatTaxRatePercent(rate) {
  if (rate === null || rate === undefined || !Number.isFinite(Number(rate))) return 'не задана';
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 }).format(Number(rate) * 100)}%`;
}

/** Secondary action only: Ozon data loads automatically from 1 January; this re-reads up to 31 days by hand. */
function OzonManualSync({ canManage }) {
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  const submit = async (event) => {
    event.preventDefault();
    if (!canManage || busy) return;
    if (!dateFrom || !dateTo || dateTo < dateFrom) {
      setMessage('Укажите даты: дата окончания не раньше даты начала.');
      return;
    }
    const days = Math.floor((Date.parse(`${dateTo}T00:00:00Z`) - Date.parse(`${dateFrom}T00:00:00Z`)) / 86400000) + 1;
    if (days > 31) {
      setMessage('За один раз можно синхронизировать не более 31 дня.');
      return;
    }
    setBusy(true);
    setMessage('');
    try {
      const result = await api.syncMarketplaceFinance(dateFrom, dateTo);
      setMessage(result?.status ? `Запрос принят: ${result.status}.` : 'Запрос на синхронизацию принят.');
    } catch (error) {
      setMessage(error?.message || 'Не удалось запустить синхронизацию Ozon.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="marketplace-settings-card" data-testid="ozon-manual-sync">
      <div className="marketplace-settings-heading">
        <div>
          <h3>Синхронизация Ozon вручную</h3>
          <p>Данные Ozon загружаются автоматически с 1 января и обновляются каждое утро. Эта кнопка нужна, чтобы перечитать отдельный период до 31 дня.</p>
        </div>
      </div>
      {canManage ? (
        <form className="marketplace-connection-form" onSubmit={submit}>
          <label>
            <span>С</span>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </label>
          <label>
            <span>По</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </label>
          <div className="marketplace-connection-actions">
            <button className="btn-outline" type="submit" disabled={busy} data-testid="ozon-manual-sync-submit">
              {busy ? 'Запускаем…' : 'Синхронизировать Ozon'}
            </button>
          </div>
        </form>
      ) : (
        <div className="marketplace-settings-readonly">Синхронизировать Ozon может владелец магазина.</div>
      )}
      {message && <div className="marketplace-notice marketplace-notice-info" role="status">{message}</div>}
    </section>
  );
}

/** Tax comes from the WB setting; operating expenses are entered per day as in WB, with no confirmation gate. */
function OzonFinancialSettings({ activeStoreOwnerId }) {
  const [taxRate, setTaxRate] = useState(null);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    api
      .getMarketplaceSettings({ signal: controller.signal })
      .then((data) => {
        setTaxRate(data?.tax_rate ?? null);
        setLoadError('');
      })
      .catch((error) => {
        if (!controller.signal.aborted) setLoadError(error?.message || 'Не удалось загрузить налоговую ставку.');
      });
    return () => controller.abort();
  }, [activeStoreOwnerId]);

  const taxText = formatTaxRatePercent(taxRate);
  return (
    <section className="marketplace-settings-card" data-testid="ozon-financial-settings">
      <div className="marketplace-settings-heading">
        <div>
          <h3>Налог и расходы Ozon</h3>
          <p>
            Ставка налога берётся из настроек Wildberries: {taxText}. Операционные расходы вносятся по дням в разделе
            «Опер. расходы»; день без записи считается нулём, как в WB.
          </p>
        </div>
      </div>
      {loadError && <div className="marketplace-notice marketplace-notice-error" role="alert">{loadError}</div>}
    </section>
  );
}

function OzonConnectionSettings({ activeStore, activeStoreOwnerId, canManage }) {
  const [connection, setConnection] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [clientId, setClientId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [loadError, setLoadError] = useState('');
  const [formError, setFormError] = useState('');
  const [saved, setSaved] = useState(false);
  const requestGeneration = useRef(0);
  const savedClientId = String(connection?.client_id || '');
  const isDirty = clientId.trim() !== savedClientId || apiKey.length > 0;
  const connectionStatus = getOzonConnectionStatus(connection, { loading, saving, loadError });

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    setLoading(true);
    setConnection(null);
    setClientId('');
    setApiKey('');
    setLoadError('');
    setFormError('');
    setSaved(false);

    api.getMarketplaceConnection({ signal: controller.signal })
      .then((result) => {
        if (generation !== requestGeneration.current) return;
        setConnection(result);
        setClientId(String(result?.client_id || ''));
      })
      .catch((error) => {
        if (generation !== requestGeneration.current || controller.signal.aborted) return;
        setLoadError(error?.message || 'Не удалось загрузить подключение Ozon.');
      })
      .finally(() => {
        if (generation === requestGeneration.current && !controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      if (generation === requestGeneration.current) requestGeneration.current += 1;
    };
  }, [activeStoreOwnerId]);

  const resetForm = () => {
    setClientId(savedClientId);
    setApiKey('');
    setFormError('');
    setSaved(false);
  };

  const save = async (event) => {
    event.preventDefault();
    if (!canManage || saving) return;
    const normalizedClientId = clientId.trim();
    const normalizedKey = apiKey;
    if (!normalizedClientId) {
      setFormError('Укажите Client ID из кабинета продавца Ozon.');
      return;
    }
    if (!normalizedKey.trim()) {
      setFormError('Введите API-ключ. Он не отображается повторно после сохранения.');
      return;
    }

    setSaving(true);
    setFormError('');
    setSaved(false);
    try {
      const result = await api.saveMarketplaceConnection(normalizedClientId, normalizedKey);
      setConnection((current) => ({
        ...current,
        ...result,
        configured: result?.configured ?? true,
        client_id: result?.client_id ?? normalizedClientId,
      }));
      setClientId(String(result?.client_id ?? normalizedClientId));
      setApiKey('');
      setSaved(true);
    } catch (error) {
      setFormError(error?.message || 'Не удалось сохранить подключение. Проверьте Client ID и ключ, затем повторите.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="marketplace-settings-card" data-testid="ozon-connection-settings">
      <div className="marketplace-settings-heading">
        <div>
          <h3>Подключение Ozon</h3>
          <p>Ключ нужен только для чтения начислений, товаров и финансовых деталей.</p>
        </div>
        <span className={`tag ${connectionStatus.className}`} data-testid="ozon-connection-status">
          {connectionStatus.label}
        </span>
      </div>

      {loadError && <div className="marketplace-notice marketplace-notice-error" role="alert">{loadError}</div>}
      {['error', 'needs_reauth'].includes(String(connection?.status || '').toLowerCase()) && connection?.error && (
        <div className="marketplace-notice marketplace-notice-error" role="alert">Ошибка подключения: {connection.error}</div>
      )}

      {canManage ? (
        <form className="marketplace-connection-form" onSubmit={save}>
          <label>
            <span>Client ID</span>
            <input
              autoComplete="off"
              value={clientId}
              onChange={(event) => { setClientId(event.target.value); setSaved(false); setFormError(''); }}
              disabled={loading || saving}
              placeholder="Client ID продавца"
              data-testid="ozon-client-id"
            />
          </label>
          <label>
            <span>API-ключ</span>
            <input
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(event) => { setApiKey(event.target.value); setSaved(false); setFormError(''); }}
              disabled={loading || saving}
              placeholder={connection?.configured ? 'Введите новый ключ для замены' : 'Вставьте API-ключ'}
              data-testid="ozon-api-key"
            />
          </label>
          <div className="marketplace-connection-actions">
            <button className="btn-primary" type="submit" disabled={loading || saving || !isDirty || !clientId.trim() || !apiKey.trim()} data-testid="ozon-connection-save">
              {saving ? 'Сохраняем…' : 'Сохранить подключение'}
            </button>
            <button className="btn-outline" type="button" disabled={saving || !isDirty} onClick={resetForm}>Сбросить</button>
          </div>
          <div className="marketplace-settings-note">API-ключ не сохраняется в браузере и очищается из формы после отправки.</div>
        </form>
      ) : (
        <div className="marketplace-settings-readonly" data-testid="ozon-connection-viewer-readonly">
          {activeStore?.owner_email ? `Магазин ${activeStore.owner_email}.` : 'Подключением может управлять только владелец магазина.'}
          {' '}Статус кабинета: {connectionStatus.label.toLowerCase()}.
        </div>
      )}
      {formError && <div className="marketplace-notice marketplace-notice-error" role="alert" data-testid="ozon-connection-form-error">{formError}</div>}
      {saved && <div className="marketplace-notice marketplace-notice-success" role="status" data-testid="ozon-connection-saved">Данные сохранены. API-ключ очищен из формы.</div>}
    </section>
  );
}

