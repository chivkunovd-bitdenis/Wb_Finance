import { NavLink } from 'react-router-dom';
import { useMemo, useState } from 'react';
import { useStore } from '../StoreContext';

export default function Sidebar({ onLogout }) {
  const { stores, loadingStores, storesError, refreshStores, activeStoreOwnerId, setActiveStoreOwnerId } = useStore();
  const [storesOpen, setStoresOpen] = useState(false);

  const storeItems = useMemo(() => {
    const list = Array.isArray(stores) ? stores : [];
    return list.map((s) => ({
      id: String(s.owner_user_id),
      label: String(s.owner_email || ''),
      access: String(s.access || ''),
    }));
  }, [stores]);

  const nav = [
    { to: '/dashboard', label: 'Дашборд', icon: '📊' },
    { to: '/articles', label: 'Артикулы', icon: '📦' },
    { to: '/funnel', label: 'Воронка', icon: '📈' },
    { to: '/costs', label: 'Себестоимость', icon: '💰' },
    { to: '/operational-expenses', label: 'Опер. расходы', icon: '🧾' },
    { to: '/billing', label: 'Подписка', icon: '💳' },
    { to: '/settings', label: 'Настройки', icon: '⚙️' },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="brand">
          WB <span>Finance Pro</span>
        </div>
      </div>

      <nav className="sidebar-nav">
        <div className="nav-section-label">Магазины</div>
        <div
          className={`nav-item ${storesOpen ? 'active' : ''}`}
          role="button"
          tabIndex={0}
          onClick={() => {
            const next = !storesOpen;
            setStoresOpen(next);
            if (next) refreshStores();
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              const next = !storesOpen;
              setStoresOpen(next);
              if (next) refreshStores();
            }
          }}
          style={{ userSelect: 'none' }}
        >
          <span className="icon">🏬</span>
          <span>Магазины</span>
        </div>
        {storesOpen && (
          <div style={{ marginLeft: 8, marginTop: 6, marginBottom: 10 }}>
            {loadingStores && <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Загрузка…</div>}
            {!loadingStores && storesError && (
              <div style={{ fontSize: 12, color: 'var(--red)' }}>{storesError}</div>
            )}
            {!loadingStores && !storesError && storeItems.length === 0 && (
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Нет доступных магазинов</div>
            )}
            {!loadingStores &&
              !storesError &&
              storeItems.map((s) => (
                <div
                  key={s.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setActiveStoreOwnerId(s.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setActiveStoreOwnerId(s.id);
                    }
                  }}
                  style={{
                    padding: '6px 8px',
                    borderRadius: 8,
                    cursor: 'pointer',
                    fontSize: 12,
                    background: String(activeStoreOwnerId) === s.id ? 'rgba(0,0,0,0.06)' : 'transparent',
                    display: 'flex',
                    gap: 6,
                    alignItems: 'center',
                  }}
                >
                  <span style={{ opacity: 0.8 }}>{s.access === 'owner' ? '🟢' : '🔵'}</span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</span>
                </div>
              ))}
          </div>
        )}

        <div className="nav-section-label">Аналитика</div>
        <NavLink
          to={nav[0].to}
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          end
        >
          <span className="icon">{nav[0].icon}</span>
          <span>{nav[0].label}</span>
        </NavLink>
        <NavLink
          to={nav[1].to}
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          end
        >
          <span className="icon">{nav[1].icon}</span>
          <span>{nav[1].label}</span>
        </NavLink>
        <NavLink
          to={nav[2].to}
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          end
        >
          <span className="icon">{nav[2].icon}</span>
          <span>{nav[2].label}</span>
        </NavLink>

        <div className="nav-section-label" style={{ marginTop: 8 }}>
          Настройки
        </div>
        <NavLink
          to={nav[3].to}
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          end
        >
          <span className="icon">{nav[3].icon}</span>
          <span>{nav[3].label}</span>
        </NavLink>
        <NavLink
          to={nav[4].to}
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          end
        >
          <span className="icon">{nav[4].icon}</span>
          <span>{nav[4].label}</span>
        </NavLink>
        <NavLink
          to={nav[5].to}
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          end
        >
          <span className="icon">{nav[5].icon}</span>
          <span>{nav[5].label}</span>
        </NavLink>
        <NavLink
          to={nav[6].to}
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          end
        >
          <span className="icon">{nav[6].icon}</span>
          <span>{nav[6].label}</span>
        </NavLink>
      </nav>

      <div className="sidebar-bottom">
        <div className="user-row">
          <div className="avatar">WB</div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 500 }}>Мой кабинет</div>
            <div
              style={{ fontSize: 11, color: 'var(--text-tertiary)', cursor: 'pointer' }}
              onClick={onLogout}
            >
              Выйти
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}

