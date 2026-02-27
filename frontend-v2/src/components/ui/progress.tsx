import { cn } from '../../lib/utils';

export function Progress({
  value,
  className
}: {
  value: number;
  className?: string;
}) {
  const safe = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
  return (
    <div
      data-slot="ui-progress"
      className={cn('ui-progress', className)}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={safe}
    >
      <div data-slot="ui-progress-fill" className="ui-progress__fill" style={{ width: `${safe}%` }} />
    </div>
  );
}
