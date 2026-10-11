export default function Topbar({
  title,
  activeStoreLabel,
  dateFromDraft,
  dateToDraft,
  setDateFromDraft,
  setDateToDraft,
  onApply,
  onOpenBilling,
  marketplace,
  onMarketplaceChange,
}) {
  return (
    <div className="topbar">
      <div className="topbar-title">
        {title}
        {activeStoreLabel && (
          <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--text-tertiary)' }}>
            Текущий магазин: <strong style={{ color: 'var(--text-secondary)' }}>{activeStoreLabel}</strong>
          </span>
        )}
      </div>
      <label className="marketplace-selector">
        <span>Маркетплейс</span>
        <select
          aria-label="Маркетплейс"
          data-testid="marketplace-selector"
          value={marketplace || 'wb'}
          onChange={(event) => onMarketplaceChange?.(event.target.value)}
        >
          <option value="wb">WB</option>
          <option value="ozon">Ozon</option>
          <option value="all">Все маркетплейсы</option>
        </select>
      </label>
      <div className="date-group">
        <input
          type="date"
          value={dateFromDraft}
          onChange={(e) => setDateFromDraft(e.target.value)}
        />
        <span className="date-sep">—</span>
        <input
          type="date"
          value={dateToDraft}
          onChange={(e) => setDateToDraft(e.target.value)}
        />
      </div>
      <button className="btn-primary" onClick={onApply}>
        Показать
      </button>
      <button className="btn-primary" onClick={onOpenBilling}>
        Подписка
      </button>
    </div>
  );
}

