import * as React from 'react';
import type { HTMLAttributes } from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cn } from '../../lib/utils';

type TypographyProps<T extends HTMLElement> = HTMLAttributes<T> & {
  asChild?: boolean;
};

export const TypographyH1 = React.forwardRef<HTMLHeadingElement, TypographyProps<HTMLHeadingElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-h1" className={cn('ui-type-h1', className)} {...props} />;
  }
  return <h1 ref={ref} data-slot="ui-typography-h1" className={cn('ui-type-h1', className)} {...props} />;
});
TypographyH1.displayName = 'TypographyH1';

export const TypographyH2 = React.forwardRef<HTMLHeadingElement, TypographyProps<HTMLHeadingElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-h2" className={cn('ui-type-h2', className)} {...props} />;
  }
  return <h2 ref={ref} data-slot="ui-typography-h2" className={cn('ui-type-h2', className)} {...props} />;
});
TypographyH2.displayName = 'TypographyH2';

export const TypographyH3 = React.forwardRef<HTMLHeadingElement, TypographyProps<HTMLHeadingElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-h3" className={cn('ui-type-h3', className)} {...props} />;
  }
  return <h3 ref={ref} data-slot="ui-typography-h3" className={cn('ui-type-h3', className)} {...props} />;
});
TypographyH3.displayName = 'TypographyH3';

export const TypographyH4 = React.forwardRef<HTMLHeadingElement, TypographyProps<HTMLHeadingElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-h4" className={cn('ui-type-h4', className)} {...props} />;
  }
  return <h4 ref={ref} data-slot="ui-typography-h4" className={cn('ui-type-h4', className)} {...props} />;
});
TypographyH4.displayName = 'TypographyH4';

export const TypographyP = React.forwardRef<HTMLParagraphElement, TypographyProps<HTMLParagraphElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-p" className={cn('ui-type-p', className)} {...props} />;
  }
  return <p ref={ref} data-slot="ui-typography-p" className={cn('ui-type-p', className)} {...props} />;
});
TypographyP.displayName = 'TypographyP';

export const TypographyLead = React.forwardRef<HTMLParagraphElement, TypographyProps<HTMLParagraphElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-lead" className={cn('ui-type-lead', className)} {...props} />;
  }
  return <p ref={ref} data-slot="ui-typography-lead" className={cn('ui-type-lead', className)} {...props} />;
});
TypographyLead.displayName = 'TypographyLead';

export const TypographyLarge = React.forwardRef<HTMLDivElement, TypographyProps<HTMLDivElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-large" className={cn('ui-type-large', className)} {...props} />;
  }
  return <div ref={ref} data-slot="ui-typography-large" className={cn('ui-type-large', className)} {...props} />;
});
TypographyLarge.displayName = 'TypographyLarge';

export const TypographySmall = React.forwardRef<HTMLElement, TypographyProps<HTMLElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-small" className={cn('ui-type-small', className)} {...props} />;
  }
  return <small ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-small" className={cn('ui-type-small', className)} {...props} />;
});
TypographySmall.displayName = 'TypographySmall';

export const TypographyMuted = React.forwardRef<HTMLParagraphElement, TypographyProps<HTMLParagraphElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-muted" className={cn('ui-type-muted', className)} {...props} />;
  }
  return <p ref={ref} data-slot="ui-typography-muted" className={cn('ui-type-muted', className)} {...props} />;
});
TypographyMuted.displayName = 'TypographyMuted';

export const TypographyBlockquote = React.forwardRef<HTMLQuoteElement, TypographyProps<HTMLQuoteElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-blockquote" className={cn('ui-type-blockquote', className)} {...props} />;
  }
  return <blockquote ref={ref} data-slot="ui-typography-blockquote" className={cn('ui-type-blockquote', className)} {...props} />;
});
TypographyBlockquote.displayName = 'TypographyBlockquote';

export const TypographyInlineCode = React.forwardRef<HTMLElement, TypographyProps<HTMLElement>>(({
  className,
  asChild = false,
  ...props
}, ref) => {
  if (asChild) {
    return <Slot ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-inline-code" className={cn('ui-type-inline-code', className)} {...props} />;
  }
  return <code ref={ref as React.Ref<HTMLElement>} data-slot="ui-typography-inline-code" className={cn('ui-type-inline-code', className)} {...props} />;
});
TypographyInlineCode.displayName = 'TypographyInlineCode';
