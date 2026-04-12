import { useState } from "react";
import { useKeyboardShortcuts, GLOBAL_SHORTCUTS } from "../hooks/useKeyboardShortcuts";

export function KeyboardShortcutsHelp() {
  const [isOpen, setIsOpen] = useState(false);

  useKeyboardShortcuts([
    {
      key: "?",
      shift: true,
      description: "Показать горячие клавиши",
      action: () => setIsOpen(true),
    },
    {
      key: "Escape",
      description: "Закрыть",
      action: () => setIsOpen(false),
    },
  ]);

  if (!isOpen) return null;

  return (
    <div className="dialog-backdrop" onClick={() => setIsOpen(false)}>
      <div className="dialog-card shortcuts-dialog" onClick={(e) => e.stopPropagation()}>
        <h2>Горячие клавиши</h2>
        <p className="muted">Используйте клавиатуру для быстрой навигации</p>
        <div className="shortcuts-grid">
          {GLOBAL_SHORTCUTS.map((shortcut) => (
            <div key={shortcut.key} className="shortcut-row">
              <kbd className="shortcut-key">
                {shortcut.shift && "Shift + "}
                {shortcut.ctrl && "Ctrl + "}
                {shortcut.alt && "Alt + "}
                {shortcut.key}
              </kbd>
              <span className="shortcut-description">{shortcut.description}</span>
            </div>
          ))}
          <div className="shortcut-row">
            <kbd className="shortcut-key">Esc</kbd>
            <span className="shortcut-description">Закрыть диалоги</span>
          </div>
          <div className="shortcut-row">
            <kbd className="shortcut-key">Enter</kbd>
            <span className="shortcut-description">Подтвердить действие</span>
          </div>
        </div>
        <div className="dialog-actions">
          <button type="button" onClick={() => setIsOpen(false)}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
