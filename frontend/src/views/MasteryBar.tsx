export default function MasteryBar({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">—</span>;
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <span
      className="bar"
      role="meter"
      aria-label="Mastery"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
    >
      <span className="bar-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}
