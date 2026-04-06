import { SectionHeader } from "./SectionHeader";
import type { QualityGlossaryItem } from "../types/api";

interface MetricGlossaryPanelProps {
  items: QualityGlossaryItem[];
}

export function MetricGlossaryPanel({ items }: MetricGlossaryPanelProps) {
  return (
    <section className="panel">
      <SectionHeader
        title="Как читать метрики"
        subtitle="Короткий словарь помогает быстро сверить Brier, преимущество и логику решения без лишней аналитической свалки."
      />
      {items.length ? (
        <div className="glossary-grid">
          {items.map((item) => (
            <article key={item.id} className="attention-card">
              <strong>{item.label}</strong>
              <p>
                <span className="muted">Что:</span> {item.what}
              </p>
              <p>
                <span className="muted">Зачем:</span> {item.why}
              </p>
              <p>
                <span className="muted">Как:</span> {item.how}
              </p>
            </article>
          ))}
        </div>
      ) : (
        <p className="muted">Словарь метрик пока пуст.</p>
      )}
    </section>
  );
}
