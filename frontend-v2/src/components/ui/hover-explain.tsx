import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react';
import { cn } from '../../lib/utils';
import { HoverCard, HoverCardContent, HoverCardTrigger } from './hover-card';

type HoverExplainProps = {
  asChild?: boolean;
  children: ReactNode;
  content: ReactNode;
  openDelay?: number;
  closeDelay?: number;
  align?: 'start' | 'center' | 'end';
  side?: 'top' | 'right' | 'bottom' | 'left';
  triggerClassName?: string;
  contentClassName?: string;
};

export function HoverExplain({
  asChild = false,
  children,
  content,
  openDelay = 80,
  closeDelay = 120,
  align = 'start',
  side = 'bottom',
  triggerClassName,
  contentClassName
}: HoverExplainProps) {
  const triggerNode = (() => {
    if (!asChild) {
      return <span className={cn('ui-hover-explain-trigger', triggerClassName)}>{children}</span>;
    }

    if (!isValidElement(children)) {
      return <span className={cn('ui-hover-explain-trigger', triggerClassName)}>{children}</span>;
    }

    const child = children as ReactElement<{ className?: string; tabIndex?: number }>;
    return cloneElement(child, {
      className: cn('ui-hover-explain-trigger', child.props.className, triggerClassName),
      tabIndex: child.props.tabIndex ?? 0
    });
  })();

  return (
    <HoverCard openDelay={openDelay} closeDelay={closeDelay}>
      <HoverCardTrigger asChild>{triggerNode}</HoverCardTrigger>
      <HoverCardContent side={side} align={align} className={cn('ui-hover-explain-content', contentClassName)}>
        {content}
      </HoverCardContent>
    </HoverCard>
  );
}
