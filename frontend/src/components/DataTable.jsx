export default function DataTable({ title, tag, children, headRight }) {
  const hasTag = tag !== null && tag !== undefined && tag !== '';
  const hasRight = hasTag || headRight;
  return (
    <div className="table-card">
      <div className="table-head-row">
        <h3>{title}</h3>
        {hasRight ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {hasTag ? <span className="tag tag-gray">{String(tag)}</span> : null}
            {headRight}
          </div>
        ) : (
          <span />
        )}
      </div>
      <div className="table-wrap">{children}</div>
    </div>
  );
}

