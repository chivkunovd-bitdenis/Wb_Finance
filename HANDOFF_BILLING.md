# Handoff: Billing screen redesign

## Что нужно сделать

Заменить файл `frontend/src/screens/Billing.jsx` содержимым ниже, затем:

```bash
cd frontend
npm run build
```

Затем задеплоить `frontend/dist/` на сервер (rsync или git push + pull на сервере).

---

## Файл: `frontend/src/screens/Billing.jsx`

```jsx
import { useState } from 'react';
import * as api from '../api';

const PLAN_PRICE = 1490;

const STATUS_CONFIG = {
  inactive: { label: 'Не активна',  color: 'var(--text-tertiary)', bg: 'var(--bg-secondary)' },
  trial:    { label: 'Демо-период', color: '#7c3aed',              bg: '#f5f3ff'              },
  active:   { label: 'Активна',     color: 'var(--green)',          bg: 'var(--green-light)'   },
  expired:  { label: 'Истекла',     color: 'var(--red)',            bg: 'var(--red-light)'     },
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
  const [loading, setLoading] = useState(false);
  const [wbApiKey, setWbApiKey] = useState('');
  const [msg, setMsg] = useState('');
  const [msgType, setMsgType] = useState('error');

  const status = billingStatus?.subscription_status || 'inactive';
  const statusCfg = STATUS_CONFIG[status] || STATUS_CONFIG.inactive;
  const daysLeft = billingStatus?.days_left ?? 0;
  const trialEndsAt = billingStatus?.trial_ends_at;
  const periodEndsAt = billingStatus?.current_period_ends_at;

  const isActive = status === 'active';
  const showTrialForm = status === 'inactive' && !trialEndsAt && !periodEndsAt;

  const handlePay = async () => {
    setLoading(true);
    setMsg('');
    try {
      const data = await api.createCheckout(PLAN_PRICE, `${window.location.origin}/billing`);
      window.location.href = data.confirmation_url;
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
      setMsg('WB API ключ сохранён. Демо-период активирован.');
      setMsgType('success');
      await onRefreshStatus?.();
    } catch (e) {
      setMsg(e?.message || 'Не удалось сохранить WB API ключ');
      setMsgType('error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Top row: Status card + Plan card */}
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

          <div style={{ marginBottom: 16 }}>
            <span style={{
              fontSize: 40, fontWeight: 600, letterSpacing: '-0.03em',
              color: (isActive || status === 'trial') ? 'var(--text-primary)' : 'var(--text-tertiary)',
            }}>
              {daysLeft}
            </span>
            <span style={{ fontSize: 14, color: 'var(--text-secondary)', marginLeft: 6 }}>дней осталось</span>
          </div>

          <div style={{ height: '0.5px', background: 'var(--border-light)', marginBottom: 16 }} />

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {trialEndsAt && (
              <Row label="Конец демо" value={formatDate(trialEndsAt)} />
            )}
            {periodEndsAt && (
              <Row label="Подписка активна до" value={formatDate(periodEndsAt)} />
            )}
            {!trialEndsAt && !periodEndsAt && (
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
              <div style={{ fontSize: 24, fontWeight: 600, letterSpacing: '-0.02em' }}>
                {PLAN_PRICE.toLocaleString('ru-RU')} ₽
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>в месяц</div>
            </div>
            <button
              className="btn-primary"
              onClick={handlePay}
              disabled={loading}
              style={{ padding: '8px 20px', fontSize: 13 }}
            >
              {loading ? 'Загрузка…' : isActive ? 'Продлить' : 'Оплатить'}
            </button>
          </div>
        </div>
      </div>

      {/* Trial activation — only when never activated */}
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

      {/* Message */}
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
```

---

## Что изменилось vs старый Billing.jsx

| Было | Стало |
|---|---|
| `<input type="number">` для суммы | Цена зафиксирована: `1 490 ₽` |
| Сырой список "Напоминания" | Убран |
| Всё в один `<div>` без структуры | Две карточки: статус + план |
| Нет визуального статуса | Цветной бейдж (серый / фиолетовый / зелёный / красный) |
| Нет счётчика дней | Крупное число дней, серое если не активно |
| Форма демо всегда видна | Показывается только когда trial не был активирован |
| Сообщения без стиля | Зелёные / красные алерты |

## Контекст

- Компонент используется в `frontend/src/Layout.jsx`
- Пропсы: `billingStatus` (объект из `/billing/status`) и `onRefreshStatus` (callback)
- Стили берутся из `frontend/src/design.css` и `frontend/src/variables.css` — ничего нового добавлять не нужно
- Backend API не тронут
