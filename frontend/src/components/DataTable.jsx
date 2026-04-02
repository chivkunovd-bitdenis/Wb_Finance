export default function DataTable({ title, tag, children }) {
  const hasTag = tag !== null && tag !== undefined && tag !== '';
  return (
    <div className="table-card">
      <div className="table-head-row">
        <h3>{title}</h3>
        {hasTag ? <span className="tag tag-gray">{String(tag)}</span> : <span />}
      </div>
      <div className="table-wrap">{children}</div>
    </div>
  );
}

