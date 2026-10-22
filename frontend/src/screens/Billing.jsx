import { useEffect, useMemo, useState } from 'react';
import * as api from '../api';
import { useStore } from '../StoreContext';

// Цена одного маркетплейса за месяц; счёт выставляется на выбранные маркетплейсы.
const MARKETPLACE_PRICE = 1490;

const MARKETPLACES = [
  { code: 'wb', label: 'Wildberries' },
  { code: 'ozon', label: 'Ozon' },
];

const STATUS_CONFIG = {
  inactive: { label: 'Не активна',        color: 'var(--text-tertiary)', bg: 'var(--bg-secondary)' },
  trial:    { label: 'Демо-период',        color: '#7c3aed',              bg: '#f5f3ff'              },
  active:   { label: 'Активна',            color: 'var(--green)',          bg: 'var(--green-light)'   },
  expired:  { label: 'Истекла',            color: 'var(--red)',            bg: 'var(--red-light)'     },
  lifetime: { label: 'Безлимитный доступ', color: '#0ea5e9',              bg: '#e0f2fe'              },
};

const INVOICE_STATUS_CONFIG = {
  issued:    { label: 'Выставлен', color: '#7c3aed',              bg: '#f5f3ff' },
  paid:      { label: 'Оплачен',   color: 'var(--green)',          bg: 'var(--green-light)' },
  cancelled: { label: 'Отменён',   color: 'var(--text-tertiary)', bg: 'var(--bg-secondary)' },
};

const PLAN_FEATURES = [
  'Дашборд с прибылью, маржой и ROI',
  'Аналитика по артикулам и воронке',
  'Учёт себестоимости и опер. расходов',
  'AI CFO — краткий аудит каждый день',
];

const EMPTY_REQUISITES = { name: '', inn: '', kpp: '', address: '' };

const inputStyle = {
  width: '100%',
  background: 'var(--bg-secondary)',
  border: '0.5px solid var(--border-mid)',
  borderRadius: 8,
  padding: '8px 12px',
  fontSize: 13,
  color: 'var(--text-primary)',
  outline: 'none',
  fontFamily: 'inherit',
  boxSizing: 'border-box',
};

const sectionLabelStyle = {
  fontSize: 11,
  fontWeight: 500,
  color: 'var(--text-tertiary)',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
};

function formatDate(dateStr) {
  if (!dateStr) return null;
  return new Date(dateStr).toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}

/** «2026-10-22» -> «22.10.2026» без сдвига часового пояса. */
function formatIsoDay(dateStr) {
  if (!dateStr) return '';
  const [year, month, day] = String(dateStr).split('-');
  return `${day}.${month}.${year}`;
}

/** Дата выставления счёта по Москве, как её видит покупатель в документе. */
function formatIssuedDay(dateTimeStr) {
  if (!dateTimeStr) return '';
  return new Date(dateTimeStr).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' });
}

function formatRub(value) {
  return `${Number(value || 0).toLocaleString('ru-RU')} ₽`;
}

function validateRequisites(form) {
  if (!form.name.trim()) return 'Укажите наименование покупателя';
  if (!/^\d{10}(\d{2})?$/.test(form.inn.trim())) return 'ИНН должен состоять из 10 или 12 цифр';
  if (form.kpp.trim() && !/^\d{9}$/.test(form.kpp.trim())) return 'КПП должен состоять из 9 цифр';
  if (!form.address.trim()) return 'Укажите адрес покупателя';
  return '';
}

/** Скачать blob в браузере через временную ссылку. */
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function Billing({ billingStatus, onRefreshStatus }) {
  const { isOwnerStore } = useStore();
  const [loading, setLoading] = useState(false);
  const [wbApiKey, setWbApiKey] = useState('');
  const [wbApiKeyLoaded, setWbApiKeyLoaded] = useState(false);
  const [showWbKey] = useState(false);
  const [msg, setMsg] = useState('');
  const [msgType, setMsgType] = useState('error');

  const [requisites, setRequisites] = useState(null);
  const [requisitesForm, setRequisitesForm] = useState(EMPTY_REQUISITES);
  const [invoices, setInvoices] = useState([]);
  const [selected, setSelected] = useState({ wb: true, ozon: false });
  const [preview, setPreview] = useState(null);
  const [invoiceBusy, setInvoiceBusy] = useState(false);

  const status = billingStatus?.subscription_status || 'inactive';
  const statusCfg = STATUS_CONFIG[status] || STATUS_CONFIG.inactive;
  const daysLeft = billingStatus?.days_left ?? 0;
  const trialEndsAt = billingStatus?.trial_ends_at;
  const periodEndsAt = billingStatus?.current_period_ends_at;

  // Доступ к выданному магазину определяет подписка владельца: сотрудник оплату не видит и не вносит.
  const isGranted = billingStatus?.access_source === 'granted';
  const isBlocked = Boolean(billingStatus?.is_access_blocked);
  const accessUntil = billingStatus?.access_until;

  const isLifetime = status === 'lifetime';
  const isActive = status === 'active';
  const showTrialForm = !isGranted && !isLifetime && status === 'inactive' && !trialEndsAt && !periodEndsAt;
  // Счета выставляются только на собственный аккаунт и только если доступ не безлимитный.
  const canInvoice = isOwnerStore && !isGranted && !isLifetime;

  const selectedCodes = MARKETPLACES.filter((m) => selected[m.code]).map((m) => m.code);
  const selectedKey = selectedCodes.join(',');
  const previewForSelection = preview && preview.key === selectedKey ? preview : null;

  const requisitesDirty = !requisites
    || ['name', 'inn', 'kpp', 'address'].some(
      (field) => String(requisitesForm[field] || '').trim() !== String(requisites[field] || '').trim(),
    );

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

  useEffect(() => {
    if (!canInvoice) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const [savedRequisites, list] = await Promise.all([
          api.getBillingRequisites(),
          api.listBillingInvoices(),
        ]);
        if (cancelled) return;
        setRequisites(savedRequisites);
        setRequisitesForm(
          savedRequisites
            ? {
              name: savedRequisites.name || '',
              inn: savedRequisites.inn || '',
              kpp: savedRequisites.kpp || '',
              address: savedRequisites.address || '',
            }
            : EMPTY_REQUISITES,
        );
        setInvoices(list);
      } catch (e) {
        if (!cancelled) {
          setMsg(e?.message || 'Не удалось загрузить реквизиты и счета');
          setMsgType('error');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [canInvoice]);

  useEffect(() => {
    if (!canInvoice || !selectedKey) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getBillingInvoicePreview(selectedKey.split(','));
        if (!cancelled) setPreview({ key: selectedKey, ...data });
      } catch {
        if (!cancelled) setPreview(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [canInvoice, selectedKey, periodEndsAt]);

  const maskedWbKey = useMemo(() => {
    const s = String(wbApiKey || '').trim();
    if (!s) return 'не задан';
    if (showWbKey) return s;
    if (s.length <= 8) return '••••••••';
    return `${s.slice(0, 4)}••••••••${s.slice(-4)}`;
  }, [wbApiKey, showWbKey]);

  const refreshInvoices = async () => {
    try {
      setInvoices(await api.listBillingInvoices());
    } catch {
      /* список обновится при следующем открытии экрана */
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

  const handleSaveRequisites = async () => {
    const error = validateRequisites(requisitesForm);
    if (error) {
      setMsg(error);
      setMsgType('error');
      return;
    }
    setInvoiceBusy(true);
    setMsg('');
    try {
      const saved = await api.saveBillingRequisites({
        name: requisitesForm.name.trim(),
        inn: requisitesForm.inn.trim(),
        kpp: requisitesForm.kpp.trim() || null,
        address: requisitesForm.address.trim(),
      });
      setRequisites(saved);
      setRequisitesForm({
        name: saved.name,
        inn: saved.inn,
        kpp: saved.kpp || '',
        address: saved.address,
      });
      setMsg('Реквизиты сохранены. Теперь можно сформировать счёт.');
      setMsgType('success');
    } catch (e) {
      setMsg(e?.message || 'Не удалось сохранить реквизиты');
      setMsgType('error');
    } finally {
      setInvoiceBusy(false);
    }
  };

  const handleCreateInvoice = async () => {
    if (selectedCodes.length === 0) {
      setMsg('Выберите хотя бы один маркетплейс');
      setMsgType('error');
      return;
    }
    if (requisitesDirty) {
      setMsg('Сохраните реквизиты покупателя, прежде чем формировать счёт');
      setMsgType('error');
      return;
    }
    setInvoiceBusy(true);
    setMsg('');
    let invoice;
    try {
      invoice = await api.createBillingInvoice(selectedCodes);
    } catch (e) {
      setMsg(e?.message || 'Не удалось сформировать счёт');
      setMsgType('error');
      setInvoiceBusy(false);
      return;
    }
    try {
      const { blob, filename } = await api.downloadBillingInvoicePdf(invoice.id);
      saveBlob(blob, filename || `Счет_${invoice.number_display}.pdf`);
      setMsg(`Счёт ${invoice.number_display} сформирован и скачан.`);
      setMsgType('success');
    } catch (e) {
      setMsg(
        `Счёт ${invoice.number_display} сформирован, но файл не скачался: ${e?.message || 'ошибка'}. `
        + 'Его можно скачать из списка ниже.',
      );
      setMsgType('error');
    } finally {
      await refreshInvoices();
      setInvoiceBusy(false);
    }
  };

  const handleDownloadInvoice = async (invoice) => {
    setInvoiceBusy(true);
    setMsg('');
    try {
      const { blob, filename } = await api.downloadBillingInvoicePdf(invoice.id);
      saveBlob(blob, filename || `Счет_${invoice.number_display}.pdf`);
    } catch (e) {
      setMsg(e?.message || 'Не удалось скачать счёт');
      setMsgType('error');
    } finally {
      setInvoiceBusy(false);
    }
  };

  const setRequisitesField = (field) => (value) => {
    setRequisitesForm((prev) => ({ ...prev, [field]: value }));
  };

  const toggleMarketplace = (code) => {
    setSelected((prev) => ({ ...prev, [code]: !prev[code] }));
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* ── Top row: Status card + Plan card ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>

        {/* Status card */}
        <div className="table-card" style={{ padding: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)' }}>
              {isGranted ? 'Доступ к магазину' : 'Статус подписки'}
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
            {isGranted && (
              <>
                <Row label="Владелец магазина" value={billingStatus?.store_owner_email || '—'} />
                {!isLifetime && accessUntil && (
                  <Row label="Доступ до" value={formatDate(accessUntil)} />
                )}
                {isBlocked ? (
                  <p style={{ fontSize: 12, color: 'var(--red)', lineHeight: 1.5 }}>
                    Доступ к магазину приостановлен: подписка владельца не активна. Обратитесь к владельцу магазина.
                  </p>
                ) : (
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    Доступ к этому магазину предоставлен владельцем по его подписке. Оплачивать ничего не нужно.
                  </p>
                )}
              </>
            )}
            {isLifetime && (
              <p style={{ fontSize: 12, color: '#0369a1', lineHeight: 1.5 }}>
                Вам предоставлен бессрочный доступ ко всем функциям платформы.
              </p>
            )}
            {!isGranted && !isLifetime && trialEndsAt && (
              <Row label="Конец демо" value={formatDate(trialEndsAt)} />
            )}
            {!isGranted && !isLifetime && periodEndsAt && (
              <Row label="Подписка активна до" value={formatDate(periodEndsAt)} />
            )}
            {!isGranted && !isLifetime && !trialEndsAt && !periodEndsAt && (
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

          <div>
            {isGranted ? (
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)' }}>
                Оплачивает владелец
              </div>
            ) : isLifetime ? (
              <>
                <div style={{ fontSize: 24, fontWeight: 600, letterSpacing: '-0.02em', color: '#0ea5e9' }}>
                  Бесплатно
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>навсегда</div>
              </>
            ) : (
              <>
                <div style={{ fontSize: 24, fontWeight: 600, letterSpacing: '-0.02em' }}>
                  {MARKETPLACE_PRICE.toLocaleString('ru-RU')} ₽
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  в месяц за каждый маркетплейс
                </div>
              </>
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

      {/* ── Invoice block: owner's own account only ── */}
      {isOwnerStore && isLifetime && (
        <div className="table-card" style={{ padding: 18 }}>
          <span style={{ fontSize: 13, color: '#0369a1' }}>Безлимитный доступ — счёт не требуется</span>
        </div>
      )}

      {canInvoice && (
        <div className="table-card" style={{ padding: 24 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
            <span style={{ fontSize: 14, fontWeight: 500 }}>Счёт на оплату</span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 24 }}>
            {/* Реквизиты покупателя */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={sectionLabelStyle}>Реквизиты для счёта</div>
              <Field
                label="Наименование покупателя"
                value={requisitesForm.name}
                onChange={setRequisitesField('name')}
                placeholder='ИП Иванов Иван Иванович или ООО «Ромашка»'
              />
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <Field
                  label="ИНН (10 или 12 цифр)"
                  value={requisitesForm.inn}
                  onChange={setRequisitesField('inn')}
                  inputMode="numeric"
                  maxLength={12}
                />
                <Field
                  label="КПП (9 цифр, необязательно)"
                  value={requisitesForm.kpp}
                  onChange={setRequisitesField('kpp')}
                  inputMode="numeric"
                  maxLength={9}
                />
              </div>
              <Field
                label="Адрес"
                value={requisitesForm.address}
                onChange={setRequisitesField('address')}
                placeholder="Индекс, город, улица, дом"
              />
              <div>
                <button
                  type="button"
                  className="btn-outline"
                  onClick={handleSaveRequisites}
                  disabled={invoiceBusy || !requisitesDirty}
                >
                  Сохранить реквизиты
                </button>
              </div>
            </div>

            {/* Маркетплейсы, сумма и формирование счёта */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={sectionLabelStyle}>Маркетплейсы</div>
              {MARKETPLACES.map((m) => (
                <label key={m.code} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={Boolean(selected[m.code])}
                    onChange={() => toggleMarketplace(m.code)}
                  />
                  <span>{`${m.label} — ${MARKETPLACE_PRICE.toLocaleString('ru-RU')} ₽/мес`}</span>
                </label>
              ))}

              <div style={{
                background: 'var(--bg-secondary)',
                borderRadius: 8,
                padding: '12px 14px',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}>
                {previewForSelection ? (
                  <>
                    <div style={{ fontSize: 14, fontWeight: 600 }}>
                      {`Итого: ${formatRub(previewForSelection.amount)}`}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                      {`Счёт на период с ${formatIsoDay(previewForSelection.period_start)} по ${formatIsoDay(previewForSelection.period_end)}`}
                    </div>
                  </>
                ) : (
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                    {selectedCodes.length === 0 ? 'Выберите маркетплейс' : 'Считаем сумму и период…'}
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={handleCreateInvoice}
                  disabled={invoiceBusy || selectedCodes.length === 0 || requisitesDirty}
                  style={{ padding: '8px 20px', fontSize: 13 }}
                >
                  {invoiceBusy ? 'Формируем счёт…' : 'Сформировать счёт'}
                </button>
                {requisitesDirty && (
                  <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                    Сохраните реквизиты покупателя, чтобы сформировать счёт.
                  </span>
                )}
              </div>

              <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                После оплаты доступ продлится на период из счёта. Оплата по счёту поступает в течение 1–3 рабочих дней.
              </p>
            </div>
          </div>

          {invoices.length > 0 && (
            <div style={{ marginTop: 22 }}>
              <div style={{ ...sectionLabelStyle, marginBottom: 8 }}>Ваши счета</div>
              <div className="table-wrap" style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left', padding: '8px 10px' }}>Номер</th>
                      <th style={{ textAlign: 'left', padding: '8px 10px' }}>Дата</th>
                      <th style={{ textAlign: 'left', padding: '8px 10px' }}>Период</th>
                      <th style={{ textAlign: 'right', padding: '8px 10px' }}>Сумма</th>
                      <th style={{ textAlign: 'left', padding: '8px 10px' }}>Статус</th>
                      <th style={{ textAlign: 'right', padding: '8px 10px' }} />
                    </tr>
                  </thead>
                  <tbody>
                    {invoices.map((invoice) => {
                      const statusInfo = INVOICE_STATUS_CONFIG[invoice.status] || INVOICE_STATUS_CONFIG.issued;
                      return (
                        <tr key={invoice.id} style={{ borderTop: '0.5px solid var(--border-light)' }}>
                          <td style={{ padding: '8px 10px', fontWeight: 600 }}>{invoice.number_display}</td>
                          <td style={{ padding: '8px 10px' }}>{formatIssuedDay(invoice.issued_at)}</td>
                          <td style={{ padding: '8px 10px' }}>
                            {`${formatIsoDay(invoice.period_start)} — ${formatIsoDay(invoice.period_end)}`}
                          </td>
                          <td style={{ padding: '8px 10px', textAlign: 'right' }}>{formatRub(invoice.amount)}</td>
                          <td style={{ padding: '8px 10px' }}>
                            <span style={{
                              fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 20,
                              color: statusInfo.color, background: statusInfo.bg,
                            }}>
                              {statusInfo.label}
                            </span>
                          </td>
                          <td style={{ padding: '8px 10px', textAlign: 'right' }}>
                            <button
                              type="button"
                              className="btn-outline"
                              onClick={() => handleDownloadInvoice(invoice)}
                              disabled={invoiceBusy}
                            >
                              Скачать
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
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

function Field({ label, value, onChange, placeholder, inputMode, maxLength }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{label}</span>
      <input
        style={inputStyle}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        inputMode={inputMode}
        maxLength={maxLength}
        autoComplete="off"
      />
    </label>
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
