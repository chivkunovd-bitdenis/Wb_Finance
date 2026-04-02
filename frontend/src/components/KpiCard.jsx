export default function KpiCard({
  label,
  value,
  delta,
  valueClassName,
  deltaColor,
  bar,
  valueStyle,
}) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${valueClassName || ''}`.trim()} style={valueStyle}>
        {value}
      </div>
      <div className="kpi-delta" style={deltaColor ? { color: deltaColor } : undefined}>
        {delta}
      </div>
      {bar && (
        <div className="kpi-bar">
          <div
            className="kpi-bar-fill"
            style={{
              width: bar.widthPct + '%',
              background: bar.background,
              opacity: bar.opacity,
            }}
          />
        </div>
      )}
    </div>
  );
}

