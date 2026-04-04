interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmText: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmText,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) {
    return null;
  }
  return (
    <div className="dialog-backdrop" role="presentation">
      <div className="dialog-card" role="dialog" aria-modal="true">
        <h3>{title}</h3>
        <p>{description}</p>
        <div className="dialog-actions">
          <button type="button" onClick={onCancel} className="ghost-btn">
            Отмена
          </button>
          <button type="button" onClick={onConfirm}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
