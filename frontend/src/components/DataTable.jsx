import TableExpandButton from './TableExpandButton';
import useFullscreenCard from './useFullscreenCard';

export default function DataTable({ title, tag, children, headRight, expandable = false }) {
  const fullscreen = useFullscreenCard();
  const hasTag = tag !== null && tag !== undefined && tag !== '';
  const hasRight = hasTag || headRight || expandable;
  return (
    <div className={fullscreen.cardClassName}>
      <div className="table-head-row">
        <h3>{title}</h3>
        {hasRight ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {expandable ? (
              <TableExpandButton expanded={fullscreen.expanded} onToggle={fullscreen.toggle} />
            ) : null}
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

