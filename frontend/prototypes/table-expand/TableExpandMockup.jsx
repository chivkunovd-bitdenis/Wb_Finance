import { useEffect, useMemo, useRef, useState } from 'react';

const TABLE_COLUMNS = [
  { label: 'Выручка', key: 'revenue', width: 80, title: 'Выручка, ₽' },
  { label: 'Заказы ₽', key: 'orders_sum', width: 80, title: 'Заказы, ₽' },
  { label: 'Ком', key: 'commission', width: 80, title: 'Комиссия, ₽' },
  { label: '% ком', key: 'commission_pct', width: 68, percent: true, title: 'Комиссия, %' },
  { label: 'Лог', key: 'logistics', width: 80, title: 'Логистика, ₽' },
  { label: '% лог', key: 'logistics_pct', width: 68, percent: true, title: 'Логистика, %' },
  { label: 'Штрафы', key: 'penalties', width: 72, title: 'Штрафы, ₽' },
  { label: 'Себес', key: 'cogs', width: 80, title: 'Себестоимость, ₽' },
  { label: 'Доля себеса', key: 'cogs_share', width: 70, percent: true, title: 'Доля себестоимости, %' },
  { label: 'Налог', key: 'tax', width: 72, title: 'Налог, ₽' },
  { label: 'Реклама', key: 'ads_spend', width: 80, title: 'Реклама, ₽' },
  { label: '% рекл', key: 'ads_pct', width: 68, percent: true, title: 'Реклама, %' },
  { label: 'Хран', key: 'storage', width: 72, title: 'Хранение, ₽' },
  { label: '% хран', key: 'storage_pct', width: 68, percent: true, title: 'Хранение, %' },
  { label: 'Доля расходов ВБ', key: 'wb_expenses_share', width: 80, percent: true, title: 'Доля расходов WB, %' },
  { label: 'Опер. расходы', key: 'operation_expenses', width: 84, title: 'Операционные расходы, ₽' },
  { label: 'Маржа', key: 'margin', width: 80, title: 'Маржа, ₽' },
  { label: '% маржи', key: 'margin_pct', width: 68, percent: true, title: 'Маржа, %' },
  { label: 'ROI', key: 'roi', width: 60, percent: true, title: 'ROI, %' },
];

const numberFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const percentFormat = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function createSyntheticRows() {
  return Array.from({ length: 12 }, (_, index) => {
    const day = index + 1;
    const revenue = 78_400 + ((index * 7_731 + (index % 3) * 4_210) % 51_800);
    const commission = Math.round(revenue * (0.115 + (index % 3) * 0.004));
    const logistics = Math.round(revenue * (0.048 + (index % 4) * 0.002));
    const penalties = [0, 540, 0, 1_190, 0, 0, 760][index % 7];
    const cogs = Math.round(revenue * (0.29 + (index % 3) * 0.015));
    const tax = Math.round(revenue * 0.06);
    const ads = Math.round(revenue * (0.061 + (index % 4) * 0.006));
    const storage = Math.round(revenue * (0.009 + (index % 3) * 0.002));
    const operationExpenses = 1_600 + (index % 4) * 430;
    const margin = revenue - commission - logistics - penalties - cogs - tax - ads - storage - operationExpenses;

    return {
      date: `2026-10-${String(day).padStart(2, '0')}`,
      revenue,
      orders_sum: Math.round(revenue * (1.035 + (index % 3) * 0.018)),
      commission,
      logistics,
      penalties,
      cogs,
      tax,
      ads_spend: ads,
      storage,
      operation_expenses: operationExpenses,
      margin,
    };
  });
}

function formatAmount(value) {
  return numberFormat.format(Math.round(Number(value) || 0));
}

function formatPercent(value) {
  return `${percentFormat.format(Number(value) || 0)}%`;
}

function dateLabel(isoDate) {
  const [, month, day] = isoDate.split('-');
  return `${day}.${month}`;
}

function marketColorClass(marketplace) {
  return marketplace === 'ozon' ? 'te-market-ozon' : 'te-market-wb';
}

function getRowValues(row) {
  const revenue = Number(row.revenue) || 0;
  const ratio = (value) => (revenue > 0 ? (Number(value) / revenue) * 100 : 0);

  return {
    ...row,
    commission_pct: ratio(row.commission),
    logistics_pct: ratio(row.logistics),
    cogs_share: ratio(row.cogs),
    ads_pct: ratio(row.ads_spend),
    storage_pct: ratio(row.storage),
    wb_expenses_share: ratio((Number(row.storage) || 0) + (Number(row.commission) || 0) + (Number(row.ads_spend) || 0) + (Number(row.logistics) || 0) + (Number(row.penalties) || 0)),
    margin_pct: ratio(row.margin),
    roi: Number(row.cogs) > 0 ? (Number(row.margin) / Number(row.cogs)) * 100 : 0,
  };
}

function makeTotals(rows) {
  const totals = rows.reduce((sum, row) => {
    for (const key of ['revenue', 'orders_sum', 'commission', 'logistics', 'penalties', 'cogs', 'tax', 'ads_spend', 'storage', 'operation_expenses', 'margin']) {
      sum[key] += Number(row[key]) || 0;
    }
    return sum;
  }, {
    revenue: 0,
    orders_sum: 0,
    commission: 0,
    logistics: 0,
    penalties: 0,
    cogs: 0,
    tax: 0,
    ads_spend: 0,
    storage: 0,
    operation_expenses: 0,
    margin: 0,
  });

  return getRowValues(totals);
}

function MarketplaceSelect({ value, onChange, compact = false }) {
  return (
    <label className={`te-control ${compact ? 'te-control-compact' : ''}`}>
      {!compact && <span>Маркетплейс</span>}
      <select aria-label="Маркетплейс" value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="wb">WB</option>
        <option value="ozon">Ozon</option>
      </select>
    </label>
  );
}

function PeriodSelect({ value, onChange, compact = false }) {
  return (
    <label className={`te-control ${compact ? 'te-control-compact te-period-control' : ''}`}>
      {!compact && <span>Период</span>}
      <select aria-label="Период" value={value} onChange={(event) => onChange(Number(event.target.value))}>
        <option value={12}>1–12 окт. 2026</option>
        <option value={7}>6–12 окт. 2026</option>
      </select>
    </label>
  );
}

function Navigation({ activeItem, onSelect }) {
  const analytics = [
    ['Дашборд', '▥'],
    ['Артикулы', '□'],
    ['Воронка', '⌁'],
    ['ИИ-чат', '◌'],
  ];
  const settings = [
    ['Себестоимость', '₽'],
    ['Опер. расходы', '↘'],
    ['Подписка', '◇'],
    ['Настройки', '⚙'],
  ];

  return (
    <aside className="te-sidebar" aria-label="Меню приложения">
      <div className="te-sidebar-logo">
        <div className="te-brand">WB <span>Finance Pro</span></div>
      </div>
      <nav className="te-sidebar-nav" aria-label="Разделы приложения">
        <div className="te-section-label">Магазины</div>
        <button className="te-nav-item" type="button" onClick={() => onSelect('Магазины')}>
          <span className="te-nav-icon">▦</span><span>Магазины</span>
        </button>
        <div className="te-section-label">Аналитика</div>
        {analytics.map(([label, icon]) => (
          <button
            key={label}
            className={`te-nav-item ${activeItem === label ? 'is-active' : ''}`}
            type="button"
            aria-current={activeItem === label ? 'page' : undefined}
            onClick={() => onSelect(label)}
          >
            <span className="te-nav-icon">{icon}</span><span>{label}</span>
          </button>
        ))}
        <div className="te-section-label te-settings-label">Настройки</div>
        {settings.map(([label, icon]) => (
          <button key={label} className="te-nav-item" type="button" onClick={() => onSelect(label)}>
            <span className="te-nav-icon">{icon}</span><span>{label}</span>
          </button>
        ))}
      </nav>
      <div className="te-sidebar-bottom">
        <div className="te-user-row">
          <div className="te-avatar">WB</div>
          <div><strong>Мой кабинет</strong><span>Демонстрационный профиль</span></div>
        </div>
      </div>
    </aside>
  );
}

function RevenueChart({ rows }) {
  const points = rows.map((row) => Number(row.revenue) || 0);
  const min = Math.min(...points);
  const max = Math.max(...points);
  const chartPoints = points.map((value, index) => {
    const x = points.length === 1 ? 400 : (index / (points.length - 1)) * 800;
    const y = 82 - ((value - min) / (max - min || 1)) * 52;
    return `${x},${y}`;
  }).join(' ');

  return (
    <section className="te-chart-card" aria-label="График продаж">
      <div className="te-chart-heading">
        <h2>Продажи, ₽</h2>
        <span>Сумма за день</span>
      </div>
      <svg viewBox="0 0 800 112" preserveAspectRatio="none" role="img" aria-label="Динамика выручки за выбранный период">
        <line x1="0" y1="30" x2="800" y2="30" />
        <line x1="0" y1="60" x2="800" y2="60" />
        <line x1="0" y1="90" x2="800" y2="90" />
        <polyline points={chartPoints} />
      </svg>
      <div className="te-chart-labels"><span>{dateLabel(rows[0]?.date || '2026-10-01')}</span><span>{dateLabel(rows.at(-1)?.date || '2026-10-12')}</span></div>
    </section>
  );
}

function Kpi({ label, value, hint, tone = 'purple' }) {
  return (
    <section className={`te-kpi te-kpi-${tone}`}>
      <div className="te-kpi-label">{label}</div>
      <div className="te-kpi-value">{value}</div>
      <div className="te-kpi-hint">{hint}</div>
      <span className="te-kpi-bar" />
    </section>
  );
}

function ExpandIcon({ expanded = false }) {
  return expanded ? (
    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2H2v4M10 2h4v4M2 10v4h4M14 10v4h-4M2.5 2.5l4 4M13.5 2.5l-4 4M2.5 13.5l4-4M13.5 13.5l-4-4" /></svg>
  ) : (
    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4M2.5 2.5l4 4M13.5 2.5l-4 4M13.5 13.5l-4-4M2.5 13.5l4-4" /></svg>
  );
}

function FinancialTable({ rows, onExpand, expandButtonRef, expanded, collapseButtonRef, onCollapse, marketplace, periodDays, setMarketplace, setPeriodDays, tableScrollRef }) {
  const dailyRows = rows.map(getRowValues);
  const totals = useMemo(() => makeTotals(rows), [rows]);

  function renderValue(row, column) {
    const value = Number(row[column.key]) || 0;
    const isMargin = column.key === 'margin' || column.key === 'margin_pct';
    const className = isMargin ? (value < 0 ? 'te-value-negative' : 'te-value-positive') : '';
    const text = column.percent ? formatPercent(value) : formatAmount(value);

    return <td key={column.key} className={className} title={column.title}>{text}</td>;
  }

  return (
    <section
      id="daily-detail-table"
      className={`te-table-card ${marketColorClass(marketplace)} ${expanded ? 'is-expanded' : ''}`}
      aria-label="Детализация по дням"
    >
      <header className="te-table-heading">
        <div className="te-table-title-block">
          <h2>Детализация по дням</h2>
          <span className="te-count-pill">{rows.length} дней</span>
          {expanded && <span className="te-synthetic-inline">Учебные данные</span>}
        </div>
        {expanded ? (
          <div className="te-expanded-actions">
            <MarketplaceSelect compact value={marketplace} onChange={setMarketplace} />
            <PeriodSelect compact value={periodDays} onChange={setPeriodDays} />
            <button
              ref={collapseButtonRef}
              className="te-expand-button te-collapse-button"
              type="button"
              aria-label="Свернуть таблицу и вернуться к дашборду"
              onClick={onCollapse}
            >
              <ExpandIcon expanded />
              <span>Свернуть</span>
              <kbd>Esc</kbd>
            </button>
          </div>
        ) : (
          <button
            ref={expandButtonRef}
            className="te-expand-button"
            type="button"
            aria-label="Развернуть таблицу на всё окно"
            aria-expanded="false"
            aria-controls="daily-detail-table"
            onClick={onExpand}
          >
            <ExpandIcon />
            <span>Развернуть</span>
          </button>
        )}
      </header>

      <div className="te-table-scroll" ref={tableScrollRef} role="region" tabIndex={0} aria-label="Финансовая таблица; доступна прокрутка">
        <table className="te-financial-table">
          <colgroup>
            <col style={{ width: 76 }} />
            {TABLE_COLUMNS.map((column) => <col key={column.key} style={{ width: column.width }} />)}
          </colgroup>
          <thead>
            <tr>
              <th className="te-date-cell" scope="col">Дата</th>
              {TABLE_COLUMNS.map((column) => <th key={column.key} scope="col" title={column.title}>{column.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {dailyRows.length === 0 ? (
              <tr><td className="te-empty-cell" colSpan={TABLE_COLUMNS.length + 1}>Нет данных за выбранный период</td></tr>
            ) : dailyRows.map((row) => (
              <tr key={row.date}>
                <td className="te-date-cell" title={row.date}>{dateLabel(row.date)}</td>
                {TABLE_COLUMNS.map((column) => renderValue(row, column))}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th className="te-date-cell" scope="row">ИТОГО</th>
              {TABLE_COLUMNS.map((column) => renderValue(totals, column, true))}
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}

export default function TableExpandMockup() {
  const [marketplace, setMarketplace] = useState('wb');
  const [periodDays, setPeriodDays] = useState(12);
  const [expanded, setExpanded] = useState(false);
  const [activeMenuItem, setActiveMenuItem] = useState('Дашборд');
  const fullRows = useMemo(() => createSyntheticRows(), []);
  const rows = useMemo(() => fullRows.slice(-periodDays), [fullRows, periodDays]);
  const totals = useMemo(() => makeTotals(rows), [rows]);
  const contentRef = useRef(null);
  const tableScrollRef = useRef(null);
  const expandButtonRef = useRef(null);
  const collapseButtonRef = useRef(null);
  const restoreRef = useRef({ contentTop: 0, tableTop: 0, tableLeft: 0 });

  const revenueShare = totals.revenue > 0 ? (totals.margin / totals.revenue) * 100 : 0;
  const roi = totals.cogs > 0 ? (totals.margin / totals.cogs) * 100 : 0;

  function expandTable() {
    restoreRef.current = {
      contentTop: contentRef.current?.scrollTop || 0,
      tableTop: tableScrollRef.current?.scrollTop || 0,
      tableLeft: tableScrollRef.current?.scrollLeft || 0,
    };
    setExpanded(true);
  }

  function collapseTable() {
    setExpanded(false);
  }

  useEffect(() => {
    if (expanded) {
      collapseButtonRef.current?.focus();
      return undefined;
    }

    if (restoreRef.current && expandButtonRef.current) {
      const frame = window.requestAnimationFrame(() => {
        if (contentRef.current) contentRef.current.scrollTop = restoreRef.current.contentTop;
        if (tableScrollRef.current) {
          tableScrollRef.current.scrollTop = restoreRef.current.tableTop;
          tableScrollRef.current.scrollLeft = restoreRef.current.tableLeft;
        }
        if (restoreRef.current.returnFocus) expandButtonRef.current.focus();
      });
      return () => window.cancelAnimationFrame(frame);
    }
    return undefined;
  }, [expanded]);

  useEffect(() => {
    if (!expanded) return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        restoreRef.current.returnFocus = true;
        collapseTable();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [expanded]);

  function collapseFromButton() {
    restoreRef.current.returnFocus = true;
    collapseTable();
  }

  return (
    <div className="te-app-shell">
      <Navigation activeItem={activeMenuItem} onSelect={setActiveMenuItem} />
      <main className="te-main">
        <header className="te-topbar" aria-hidden={expanded ? 'true' : undefined} inert={expanded}>
          <div className="te-page-title">Дашборд <span className="te-demo-mark">Макет</span></div>
          <MarketplaceSelect value={marketplace} onChange={setMarketplace} />
          <PeriodSelect value={periodDays} onChange={setPeriodDays} />
        </header>

        <div className="te-content" ref={contentRef}>
          <div className="te-dashboard-chrome" aria-hidden={expanded ? 'true' : undefined} inert={expanded}>
            <div className="te-kpi-grid">
              <Kpi label="Выручка" value={`${formatAmount(totals.revenue)} ₽`} hint="за выбранный период" />
              <Kpi label="Маржа" value={`${formatAmount(totals.margin)} ₽`} hint={`${revenueShare.toFixed(1)}% от выручки`} tone={totals.margin < 0 ? 'negative' : 'positive'} />
              <Kpi label="ROI" value={formatPercent(roi)} hint="за выбранный период" />
            </div>
            <RevenueChart rows={rows} />
            <div className="te-data-note"><span aria-hidden="true">●</span> Учебные данные, все значения вымышлены</div>
          </div>
          <FinancialTable
            rows={rows}
            onExpand={expandTable}
            expandButtonRef={expandButtonRef}
            expanded={expanded}
            collapseButtonRef={collapseButtonRef}
            onCollapse={collapseFromButton}
            marketplace={marketplace}
            periodDays={periodDays}
            setMarketplace={setMarketplace}
            setPeriodDays={setPeriodDays}
          tableScrollRef={tableScrollRef}
          />
        </div>
      </main>
    </div>
  );
}
