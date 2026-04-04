import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";

const NAV_ITEMS = [
  { to: "/", label: "Сводка" },
  { to: "/runs/latest", label: "Сигналы" },
  { to: "/watchlist", label: "Избранное" },
  { to: "/operations", label: "Операции" },
];

export function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    let active = true;
    async function verifyAuth() {
      try {
        const me = await api.me();
        if (!me.authenticated && active) {
          navigate("/login");
          return;
        }
      } catch {
        if (active) {
          navigate("/login");
          return;
        }
      }
      if (active) {
        setAuthChecked(true);
      }
    }
    verifyAuth();
    return () => {
      active = false;
    };
  }, [navigate, location.pathname]);

  async function handleLogout() {
    try {
      await api.logout();
    } finally {
      navigate("/login");
    }
  }

  if (!authChecked) {
    return (
      <div className="app-shell">
        <section className="content-shell">
          <div className="panel">Проверяем доступ...</div>
        </section>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <aside className="side-nav">
        <div className="brand">Pythia</div>
        <p className="brand-sub">Панель оператора</p>
        <nav className="nav-list">
          {NAV_ITEMS.map((item) => {
            const isActive = location.pathname === item.to || (item.to !== "/" && location.pathname.startsWith(item.to));
            return (
              <Link key={item.to} className={isActive ? "nav-link is-active" : "nav-link"} to={item.to}>
                {item.label}
              </Link>
            );
          })}
        </nav>
        <button type="button" className="ghost-btn" onClick={handleLogout}>
          Выйти
        </button>
      </aside>
      <section className="content-shell">
        <Outlet />
      </section>
    </div>
  );
}
