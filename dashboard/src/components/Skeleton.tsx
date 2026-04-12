export function SkeletonCard() {
  return (
    <div className="attention-card">
      <div className="skeleton skeleton-text large" />
      <div className="skeleton skeleton-text" />
      <div className="skeleton skeleton-text small" />
    </div>
  );
}

export function SkeletonKpiCard() {
  return (
    <div className="kpi-card">
      <div className="skeleton skeleton-text small" />
      <div className="skeleton skeleton-text large" style={{ marginTop: "10px", width: "40%" }} />
      <div className="skeleton skeleton-text" style={{ marginTop: "10px" }} />
    </div>
  );
}

export function SkeletonGrid({ count = 4 }: { count?: number }) {
  return (
    <div className="attention-card-grid">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}
