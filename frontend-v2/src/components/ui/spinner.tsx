import { cn } from '../../lib/utils';

type SpinnerSize = 'sm' | 'md' | 'lg';

export function Spinner({
  className,
  size = 'md',
  label = '加载中'
}: {
  className?: string;
  size?: SpinnerSize;
  label?: string;
}) {
  return (
    <span
      data-slot="ui-spinner"
      className={cn('ui-spinner', `ui-spinner--${size}`, className)}
      role="status"
      aria-label={label}
    />
  );
}
