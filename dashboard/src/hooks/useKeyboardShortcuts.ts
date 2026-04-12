import { useEffect } from "react";

interface KeyboardShortcut {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  alt?: boolean;
  action: () => void;
  description: string;
}

export function useKeyboardShortcuts(shortcuts: KeyboardShortcut[], enabled = true) {
  useEffect(() => {
    if (!enabled) return;

    function handleKeyDown(event: KeyboardEvent) {
      for (const shortcut of shortcuts) {
        const ctrlMatch = shortcut.ctrl ? event.ctrlKey || event.metaKey : !event.ctrlKey && !event.metaKey;
        const shiftMatch = shortcut.shift ? event.shiftKey : !event.shiftKey;
        const altMatch = shortcut.alt ? event.altKey : !event.altKey;

        if (event.key === shortcut.key && ctrlMatch && shiftMatch && altMatch) {
          event.preventDefault();
          shortcut.action();
          break;
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [shortcuts, enabled]);
}

export const GLOBAL_SHORTCUTS: KeyboardShortcut[] = [
  { key: "1", description: "Перейти на Сводку", action: () => window.location.hash = "#/" },
  { key: "2", description: "Перейти на Избранное", action: () => window.location.hash = "#/watchlist" },
  { key: "3", description: "Перейти на Сигналы", action: () => window.location.hash = "#/signals" },
  { key: "4", description: "Перейти на Операции", action: () => window.location.hash = "#/operations" },
  { key: "5", description: "Перейти на Качество", action: () => window.location.hash = "#/quality" },
  { key: "?", shift: true, description: "Показать горячие клавиши", action: () => {} }, // Will be handled by component
];
