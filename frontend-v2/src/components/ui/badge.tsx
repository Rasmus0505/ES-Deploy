import type { HTMLAttributes } from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../lib/utils';

const badgeVariants = cva('ui-badge', {
  variants: {
    tone: {
      default: 'ui-badge--default',
      success: 'ui-badge--success',
      warning: 'ui-badge--warning',
      danger: 'ui-badge--danger',
      info: 'ui-badge--info'
    }
  },
  defaultVariants: {
    tone: 'default'
  }
});

type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>['tone']>;

export function Badge({
  className,
  tone,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  return <span data-slot="ui-badge" className={cn(badgeVariants({ tone }), className)} {...props} />;
}
