import { NavLink } from 'react-router-dom';

export default function Sidebar({ onLogout }) {
  const nav = [
    { to: '/dashboard', label: 'Дашборд', icon: '📊' },
    { to: '/articles', label: 'Артикулы', icon: '📦' },
    { to: '/funnel', label: 'Воронка', icon: '📈' },
    { to: '/costs', label: 'Себестоимость', icon: '💰' },
    { to: '/operational-expenses', label: 'Опер. расходы', icon: '🧾' },
    { to: '/billing', label: 'Подписка', icon: '💳' },
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="brand">
          WB <span>Finance Pro</span>
        </div>
      </div>

      <nav className="sidebar-nav">
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

