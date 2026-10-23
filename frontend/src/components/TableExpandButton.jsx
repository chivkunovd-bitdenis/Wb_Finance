export default function TableExpandButton({ expanded, onToggle }) {
  const title = expanded ? 'Свернуть таблицу' : 'Развернуть таблицу';
  return (
    <button
      type="button"
      className="btn-outline table-expand-btn"
      aria-label={title}
      aria-expanded={expanded}
      title={title}
      onClick={onToggle}
    >
      <span className="table-expand-icon" aria-hidden="true">⤢</span>
      <span className="table-expand-label">{expanded ? 'Свернуть' : 'Развернуть'}</span>
    </button>
  );
}
