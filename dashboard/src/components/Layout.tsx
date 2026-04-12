import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useToast } from "../hooks/useToast";
import { ToastContainer } from "./Toast";
import { KeyboardShortcutsHelp } from "./KeyboardShortcutsHelp";
import { useKeyboardShortcuts } from "../hooks/useKeyboardShortcuts";

const NAV_ITEMS = [
  { to: "/", label: "Сводка" },
  { to: "/runs/latest", label: "Сигналы" },
  { to: "/quality", label: "Качество" },
  { to: "/watchlist", label: "Избранное" },
  { to: "/operations", label: "Операции" },
];

export function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const toast = useToast();

  // Keyboard shortcuts for navigation
  useKeyboardShortcuts([
    { key: "1", description: "Сводка", action: () => navigate("/") },
    { key: "2", description: "Избранное", action: () => navigate("/watchlist") },
    { key: "3", description: "Сигналы", action: () => navigate("/runs/latest") },
    { key: "4", description: "Операции", action: () => navigate("/operations") },
    { key: "5", description: "Качество", action: () => navigate("/quality") },
  ]);

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
      toast.success("Вы успешно вышли из системы");
    } catch {
      toast.error("Ошибка при выходе");
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
    <>
      <div className="app-shell">
        <aside className="side-nav">
          <div className="brand">Pythia</div>
          <p className="brand-sub">Панель оператора</p>
          <nav className="nav-list">
            {NAV_ITEMS.map((item, index) => {
              const isActive = location.pathname === item.to || (item.to !== "/" && location.pathname.startsWith(item.to));
              return (
                <Link key={item.to} className={isActive ? "nav-link is-active" : "nav-link"} to={item.to}>
                  <span className="nav-link-shortcut">{index + 1}</span>
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
      <ToastContainer toasts={toast.toasts} onRemove={toast.removeToast} />
      <KeyboardShortcutsHelp />
    </>
  );
}
