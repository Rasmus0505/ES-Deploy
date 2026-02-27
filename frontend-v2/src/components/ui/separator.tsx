import * as React from 'react';
import { cn } from '../../lib/utils';

type SeparatorProps = React.HTMLAttributes<HTMLDivElement> & {
  orientation?: 'horizontal' | 'vertical';
  decorative?: boolean;
};

const Separator = React.forwardRef<HTMLDivElement, SeparatorProps>(function Separator(
  { className, orientation = 'horizontal', decorative = true, ...props },
  ref
) {
  return (
    <div
      ref={ref}
      data-slot="ui-separator"
      data-orientation={orientation}
      role={decorative ? 'none' : 'separator'}
      aria-orientation={orientation}
      className={cn('ui-separator', className)}
      {...props}
    />
  );
});

export { Separator };
