import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '../../lib/utils';
import { HoverExplain } from './hover-explain';
import { TypographyH3, TypographyMuted } from './typography';

export function Card({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <section
      data-slot="card"
      className={cn('bg-card text-card-foreground flex flex-col gap-4 rounded-xl border py-5 shadow-sm', className)}
      {...props}
    />
  );
}

export function CardHeader({
  className,
  title,
  subtitle,
  action,
  subtitleBehavior = 'hover'
}: {
  className?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  subtitleBehavior?: 'hover' | 'inline';
}) {
  return (
    <header
      data-slot="card-header"
      className={cn(
        '@container/card-header grid auto-rows-min grid-rows-[auto_auto] items-start gap-2 px-5 has-data-[slot=card-action]:grid-cols-[1fr_auto]',
        className
      )}
    >
      <div className="ui-card__header-text min-w-0">
        <div className="ui-card__title-row flex flex-wrap items-center gap-2">
          {subtitle && subtitleBehavior === 'hover' ? (
            <HoverExplain
              asChild
              content={<TypographyMuted className="ui-card__subtitle ui-card__subtitle--hover">{subtitle}</TypographyMuted>}
              contentClassName="ui-card__subtitle-content"
            >
              <TypographyH3 className="ui-card__title ui-card__title--hover-trigger">{title}</TypographyH3>
            </HoverExplain>
          ) : (
            <TypographyH3 className="ui-card__title">{title}</TypographyH3>
          )}
        </div>
        {subtitle && subtitleBehavior === 'inline' ? <TypographyMuted className="ui-card__subtitle">{subtitle}</TypographyMuted> : null}
      </div>
      {action ? <div data-slot="card-action" className="ui-card__action col-start-2 row-span-2 row-start-1 self-start justify-self-end">{action}</div> : null}
    </header>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div data-slot="card-content" className={cn('ui-card__body px-5', className)} {...props} />;
}
