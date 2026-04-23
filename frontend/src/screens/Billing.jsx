import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import * as api from '../api';
import { useStore } from '../StoreContext';

const PLAN_PRICE = 1490;

const STATUS_CONFIG = {
  inactive: { label: 'Не активна',        color: 'var(--text-tertiary)', bg: 'var(--bg-secondary)' },
  trial:    { label: 'Демо-период',        color: '#7c3aed',              bg: '#f5f3ff'              },
  active:   { label: 'Активна',            color: 'var(--green)',          bg: 'var(--green-light)'   },
  expired:  { label: 'Истекла',            color: 'var(--red)',            bg: 'var(--red-light)'     },
  lifetime: { label: 'Безлимитный доступ', color: '#0ea5e9',              bg: '#e0f2fe'              },
};

const PLAN_FEATURES = [
  'Дашборд с прибылью, маржой и ROI',
  'Аналитика по артикулам и воронке',
  'Учёт себестоимости и опер. расходов',
  'AI CFO — краткий аудит каждый день',
];

function formatDate(dateStr) {
  if (!dateStr) return null;
  return new Date(dateStr).toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}

export default function Billing({ billingStatus, onRefreshStatus }) {
  useStore();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [wbApiKey, setWbApiKey] = useState('');
  const [wbApiKeyLoaded, setWbApiKeyLoaded] = useState(false);
  const [showWbKey, setShowWbKey] = useState(false);
  const [msg, setMsg] = useState('');
  const [msgType, setMsgType] = useState('error');

  const status = billingStatus?.subscription_status || 'inactive';
  const statusCfg = STATUS_CONFIG[status] || STATUS_CONFIG.inactive;
  const daysLeft = billingStatus?.days_left ?? 0;
  const trialEndsAt = billingStatus?.trial_ends_at;
  const periodEndsAt = billingStatus?.current_period_ends_at;

  const isLifetime = status === 'lifetime';
  const isActive = status === 'active';
  const showTrialForm = !isLifetime && status === 'inactive' && !trialEndsAt && !periodEndsAt;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.getMe();
        if (cancelled) return;
        const current = String(me?.wb_api_key || '').trim();
        setWbApiKey(current);
        setWbApiKeyLoaded(true);
      } catch {
        if (!cancelled) setWbApiKeyLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const maskedWbKey = useMemo(() => {
    const s = String(wbApiKey || '').trim();
    if (!s) return 'не задан';
    if (showWbKey) return s;
    if (s.length <= 8) return '••••••••';
    return `${s.slice(0, 4)}••••••••${s.slice(-4)}`;
  }, [wbApiKey, showWbKey]);

  useEffect(() => {
    if (searchParams.get('payment') !== 'return') return;
    let cancelled = false;
    (async () => {
      try {
        const sync = await api.syncYookassaReturn();
        if (cancelled) return;
        if (sync.activated) {
          setMsg('Оплата прошла успешно, подписка активирована.');
          setMsgType('success');
        } else if (sync.detail === 'still_pending') {
          setMsg('Платёж ещё обрабатывается. Обновите страницу через минуту или дождитесь уведомления.');
          setMsgType('error');
        } else if (sync.detail === 'canceled') {
          setMsg('Платёж отменён.');
          setMsgType('error');
        }
      } catch (e) {
        if (!cancelled) {
          setMsg(e?.message || 'Не удалось проверить статус оплаты');
          setMsgType('error');
        }
      } finally {
        if (!cancelled) await onRefreshStatus?.();
        if (!cancelled) navigate('/billing', { replace: true });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [searchParams, navigate, onRefreshStatus]);

  const handlePay = async () => {
    setLoading(true);
    setMsg('');
    try {
      const returnUrl = `${window.location.origin}/billing?payment=return`;
      const data = await api.createCheckout(PLAN_PRICE, returnUrl);
      const url = String(data.confirmation_url || '').trim();
      // Mock API без ключей ЮKassa раньше отдавал тот же return_url → ложный «возврат» и сброс UI.
      if (!url || url === returnUrl) {
        setMsg(
          'Переход к оплате невозможен: на сервере не заданы YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY в backend/.env '
          + 'или платёж вернулся без ссылки на ЮKassa. Проверьте настройки магазина.',
        );
        setMsgType('error');
        return;
      }
      window.location.assign(url);
    } catch (e) {
      setMsg(e?.message || 'Не удалось создать оплату');
      setMsgType('error');
    } finally {
      setLoading(false);
    }
  };

  const handleSaveWbKey = async () => {
    setLoading(true);
    setMsg('');
    try {
      await api.updateWbApiKey(wbApiKey);
      setMsg('WB API ключ сохранён. Запускаем первичную синхронизацию…');
      setMsgType('success');
      await onRefreshStatus?.();
      try {
        await api.triggerInitialSync();
        setMsg('WB API ключ сохранён. Первичная синхронизация запущена (как при входе).');
        setMsgType('success');
      } catch (e2) {
        setMsg(
          'WB API ключ сохранён, но синхронизация не стартовала: '
          + (e2?.message || 'проверь очередь задач (redis/celery_worker)'),
        );
        setMsgType('error');
      }
    } catch (e) {
      setMsg(e?.message || 'Не удалось сохранить WB API ключ');
      setMsgType('error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* ── Top row: Status card + Plan card ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>

        {/* Status card */}
        <div className="table-card" style={{ padding: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)' }}>
              Статус подписки
            </span>
            <span style={{
              fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 20,
              color: statusCfg.color, background: statusCfg.bg, letterSpacing: '0.03em',
            }}>
              {statusCfg.label}
            </span>
          </div>

          {/* Days left — big number or ∞ for lifetime */}
          <div style={{ marginBottom: 16 }}>
            <span style={{
              fontSize: 40, fontWeight: 600, letterSpacing: '-0.03em',
              color: isLifetime ? '#0ea5e9' : (isActive || status === 'trial') ? 'var(--text-primary)' : 'var(--text-tertiary)',
            }}>
              {isLifetime ? '∞' : daysLeft}
            </span>
            {!isLifetime && (
              <span style={{ fontSize: 14, color: 'var(--text-secondary)', marginLeft: 6 }}>дней осталось</span>
            )}
          </div>

          <div style={{ height: '0.5px', background: 'var(--border-light)', marginBottom: 16 }} />

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {isLifetime && (
              <p style={{ fontSize: 12, color: '#0369a1', lineHeight: 1.5 }}>
                Вам предоставлен бессрочный доступ ко всем функциям платформы.
              </p>
            )}
            {!isLifetime && trialEndsAt && (
              <Row label="Конец демо" value={formatDate(trialEndsAt)} />
            )}
            {!isLifetime && periodEndsAt && (
              <Row label="Подписка активна до" value={formatDate(periodEndsAt)} />
            )}
            {!isLifetime && !trialEndsAt && !periodEndsAt && (
              <p style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                Подключите WB API ключ ниже, чтобы активировать 5-дневное демо.
              </p>
            )}
          </div>
        </div>

        {/* Plan card */}
        <div className="table-card" style={{ padding: 24 }}>
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-tertiary)', letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 6 }}>
              Тарифный план
            </div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>WB Finance Pro</div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 20 }}>
            {PLAN_FEATURES.map((f) => (
              <div key={f} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12 }}>
                <span style={{ color: 'var(--green)', fontWeight: 700, flexShrink: 0, lineHeight: '18px' }}>✓</span>
                <span style={{ color: 'var(--text-secondary)', lineHeight: '18px' }}>{f}</span>
              </div>
            ))}
          </div>

          <div style={{ height: '0.5px', background: 'var(--border-light)', marginBottom: 20 }} />

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              {isLifetime ? (
                <>
                  <div style={{ fontSize: 24, fontWeight: 600, letterSpacing: '-0.02em', color: '#0ea5e9' }}>
                    Бесплатно
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>навсегда</div>
                </>
              ) : (
                <>
                  <div style={{ fontSize: 24, fontWeight: 600, letterSpacing: '-0.02em' }}>
                    {PLAN_PRICE.toLocaleString('ru-RU')} ₽
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>в месяц</div>
                </>
              )}
            </div>
            {!isLifetime && (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={handlePay}
                  disabled={loading}
                  style={{ padding: '8px 20px', fontSize: 13 }}
                >
                  {loading ? 'Переход к ЮKassa…' : isActive ? 'Продлить в ЮKassa' : 'Оплатить в ЮKassa'}
                </button>
                <span style={{ fontSize: 11, color: 'var(--text-tertiary)', textAlign: 'right', maxWidth: 220, lineHeight: 1.4 }}>
                  Карта, СБП и другие способы — через платёжный сервис{' '}
                  <a
                    href="https://yookassa.ru/"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: 'var(--text-secondary)', fontWeight: 600 }}
                  >
                    ЮKassa
                  </a>
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Trial activation (only when never activated) ── */}
      {showTrialForm && (
        <div className="table-card" style={{ padding: 24 }}>
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
            <div style={{
              width: 40, height: 40, borderRadius: 10,
              background: 'var(--accent-light)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 18, flexShrink: 0,
            }}>
              🔑
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>
                Активировать 5-дневное демо
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 14, lineHeight: 1.6 }}>
                Вставьте WB API ключ, чтобы запустить пробный период и увидеть данные вашего магазина.
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  style={{
                    flex: 1,
                    background: 'var(--bg-secondary)',
                    border: '0.5px solid var(--border-mid)',
                    borderRadius: 8,
                    padding: '8px 12px',
                    fontSize: 13,
                    color: 'var(--text-primary)',
                    outline: 'none',
                    fontFamily: 'inherit',
                  }}
                  value={wbApiKey}
                  onChange={(e) => setWbApiKey(e.target.value)}
                  placeholder="Вставьте WB API ключ"
                />
                <button
                  className="btn-primary"
                  onClick={handleSaveWbKey}
                  disabled={loading || !wbApiKey.trim()}
                  style={{ whiteSpace: 'nowrap' }}
                >
                  {loading ? 'Сохраняем…' : 'Сохранить и активировать'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── WB API key (editable row inside subscription section) ── */}
      <div className="table-card" style={{ padding: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>WB API ключ</div>
            <div style={{ fontSize: 13, color: 'var(--text-primary)', fontWeight: 600, letterSpacing: '0.01em' }}>
              {wbApiKeyLoaded ? maskedWbKey : '…'}
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flex: 1, minWidth: 280, justifyContent: 'flex-end' }}>
            <input
              style={{
                flex: 1,
                background: 'var(--bg-secondary)',
                border: '0.5px solid var(--border-mid)',
                borderRadius: 8,
                padding: '8px 12px',
                fontSize: 13,
                color: 'var(--text-primary)',
                outline: 'none',
                fontFamily: 'inherit',
                maxWidth: 520,
              }}
              value={wbApiKey}
              onChange={(e) => setWbApiKey(e.target.value)}
              placeholder="Вставьте новый WB API ключ"
              autoComplete="off"
            />
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setShowWbKey((v) => !v)}
              disabled={!wbApiKeyLoaded || !String(wbApiKey || '').trim()}
              style={{ whiteSpace: 'nowrap' }}
            >
              {showWbKey ? 'Скрыть' : 'Показать'}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={handleSaveWbKey}
              disabled={loading || !String(wbApiKey || '').trim()}
              style={{ whiteSpace: 'nowrap' }}
            >
              {loading ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
        </div>
      </div>

      {/* ── Message ── */}
      {msg && (
        <div style={{
          padding: '12px 16px',
          borderRadius: 8,
          fontSize: 13,
          background: msgType === 'success' ? 'var(--green-light)' : 'var(--red-light)',
          color:      msgType === 'success' ? 'var(--green)'       : 'var(--red)',
          border:     `0.5px solid ${msgType === 'success' ? '#86efac' : '#fca5a5'}`,
        }}>
          {msg}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, alignItems: 'center' }}>
      <span style={{ color: 'var(--text-tertiary)' }}>{label}</span>
      <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{value}</span>
    </div>
  );
}
