import './kbd.css';
import type { ComponentProps } from 'react';
import { cn } from '../../lib/utils';

export function Kbd({ className, ...props }: ComponentProps<'kbd'>) {
  return <kbd data-slot="kbd" className={cn('ui-kbd', className)} {...props} />;
}

export function KbdGroup({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="kbd-group" className={cn('ui-kbd-group', className)} {...props} />;
}
