import * as SelectPrimitive from '@radix-ui/react-select';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';
import {
  Children,
  forwardRef,
  isValidElement,
  useMemo,
  type AriaAttributes,
  type FocusEventHandler,
  type KeyboardEventHandler,
  type ReactNode
} from 'react';
import { cn } from '../../lib/utils';

type SelectChangeEvent = {
  target: { value: string };
  currentTarget: { value: string };
};

type SelectOption = {
  value: string;
  label: ReactNode;
  disabled: boolean;
};

type SelectEntry =
  | { type: 'option'; option: SelectOption }
  | { type: 'group'; label: ReactNode; options: SelectOption[] };

type SelectProps = AriaAttributes & {
  id?: string;
  name?: string;
  className?: string;
  children?: ReactNode;
  value?: string | number | readonly string[];
  defaultValue?: string | number | readonly string[];
  disabled?: boolean;
  required?: boolean;
  autoFocus?: boolean;
  title?: string;
  placeholder?: string;
  onBlur?: FocusEventHandler<HTMLButtonElement>;
  onFocus?: FocusEventHandler<HTMLButtonElement>;
  onKeyDown?: KeyboardEventHandler<HTMLButtonElement>;
  onChange?: (event: SelectChangeEvent) => void;
};

function normalizeOption(node: ReactNode, inheritedDisabled = false): SelectOption | null {
  if (!isValidElement(node) || node.type !== 'option') return null;

  const rawValue = node.props.value;
  const value = String(rawValue ?? node.props.children ?? '').trim();
  if (!value) return null;

  return {
    value,
    label: node.props.children ?? value,
    disabled: inheritedDisabled || Boolean(node.props.disabled)
  };
}

function parseEntries(children: ReactNode): SelectEntry[] {
  const entries: SelectEntry[] = [];

  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;

    if (child.type === 'optgroup') {
      const groupDisabled = Boolean(child.props.disabled);
      const groupOptions: SelectOption[] = [];
      Children.forEach(child.props.children, (groupChild) => {
        const normalized = normalizeOption(groupChild, groupDisabled);
        if (normalized) groupOptions.push(normalized);
      });

      if (groupOptions.length > 0) {
        entries.push({
          type: 'group',
          label: child.props.label || '分组',
          options: groupOptions
        });
      }
      return;
    }

    const normalized = normalizeOption(child);
    if (normalized) {
      entries.push({
        type: 'option',
        option: normalized
      });
    }
  });

  return entries;
}

function buildCompatEvent(value: string): SelectChangeEvent {
  return {
    target: { value },
    currentTarget: { value }
  };
}

export const Select = forwardRef<HTMLButtonElement, SelectProps>(function Select(
  {
    className,
    children,
    value,
    defaultValue,
    onChange,
    placeholder,
    disabled,
    id,
    name,
    required,
    onBlur,
    onFocus,
    onKeyDown,
    autoFocus,
    title,
    'aria-label': ariaLabel,
    'aria-labelledby': ariaLabelledBy,
    'aria-describedby': ariaDescribedBy
  },
  ref
) {
  const controlledValue = typeof value === 'string' ? value : value === undefined ? undefined : String(value);
  const initialValue = defaultValue === undefined ? undefined : String(defaultValue);
  const entries = useMemo(() => parseEntries(children), [children]);

  const normalizedEntries = useMemo(() => {
    const optionValues = new Set<string>();
    entries.forEach((entry) => {
      if (entry.type === 'option') {
        optionValues.add(entry.option.value);
        return;
      }
      entry.options.forEach((option) => optionValues.add(option.value));
    });

    if (!controlledValue || optionValues.has(controlledValue)) return entries;
    return [
      {
        type: 'option',
        option: {
          value: controlledValue,
          label: `${controlledValue}（当前值）`,
          disabled: false
        }
      } satisfies SelectEntry,
      ...entries
    ];
  }, [controlledValue, entries]);

  const renderOption = (option: SelectOption, key: string) => (
    <SelectPrimitive.Item
      key={key}
      value={option.value}
      disabled={option.disabled}
      data-slot="select-item"
      className={cn(
        'focus:bg-accent focus:text-accent-foreground [&_svg:not([class*=\'text-\'])]:text-muted-foreground relative flex w-full cursor-default items-center gap-2 rounded-sm py-1.5 pr-8 pl-2 text-sm outline-hidden select-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*=\'size-\'])]:size-4 *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2'
      )}
    >
      <span data-slot="select-item-indicator" className="absolute right-2 flex size-3.5 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <Check className="size-4" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );

  return (
    <SelectPrimitive.Root
      value={controlledValue}
      defaultValue={controlledValue === undefined ? initialValue : undefined}
      disabled={disabled}
      name={name}
      required={required}
      onValueChange={(nextValue) => onChange?.(buildCompatEvent(nextValue))}
    >
      <SelectPrimitive.Trigger
        ref={ref}
        id={id}
        autoFocus={autoFocus}
        onBlur={onBlur}
        onFocus={onFocus}
        onKeyDown={onKeyDown}
        title={title}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        data-slot="select-trigger"
        data-size="default"
        className={cn(
          'border-input data-[placeholder]:text-muted-foreground [&_svg:not([class*=\'text-\'])]:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:bg-input/30 dark:hover:bg-input/50 flex w-full items-center justify-between gap-2 rounded-md border bg-transparent px-3 py-2 text-sm whitespace-nowrap shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 data-[size=default]:h-9 *:data-[slot=select-value]:line-clamp-1 *:data-[slot=select-value]:flex *:data-[slot=select-value]:items-center *:data-[slot=select-value]:gap-2 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*=\'size-\'])]:size-4',
          className
        )}
      >
        <SelectPrimitive.Value data-slot="select-value" placeholder={placeholder || '请选择'} />
        <SelectPrimitive.Icon asChild>
          <ChevronDown className="size-4 opacity-50" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          data-slot="select-content"
          position="popper"
          sideOffset={6}
          align="center"
          className={cn(
            'bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 relative z-50 max-h-(--radix-select-content-available-height) min-w-[8rem] origin-(--radix-select-content-transform-origin) overflow-x-hidden overflow-y-auto rounded-md border shadow-md data-[side=bottom]:translate-y-1 data-[side=left]:-translate-x-1 data-[side=right]:translate-x-1 data-[side=top]:-translate-y-1'
          )}
        >
          <SelectPrimitive.ScrollUpButton data-slot="select-scroll-up-button" className="flex cursor-default items-center justify-center py-1">
            <ChevronUp className="size-4" />
          </SelectPrimitive.ScrollUpButton>
          <SelectPrimitive.Viewport className="h-[var(--radix-select-trigger-height)] w-full min-w-[var(--radix-select-trigger-width)] scroll-my-1 p-1">
            {normalizedEntries.length === 0 ? (
              <div className="text-muted-foreground px-2 py-2 text-xs">暂无可选项</div>
            ) : (
              normalizedEntries.map((entry, index) => {
                if (entry.type === 'option') {
                  return renderOption(entry.option, `${entry.option.value}-${index}`);
                }
                return (
                  <SelectPrimitive.Group key={`group-${index}`}>
                    <SelectPrimitive.Label data-slot="select-label" className="text-muted-foreground px-2 py-1.5 text-xs">
                      {entry.label}
                    </SelectPrimitive.Label>
                    {entry.options.map((option, optionIndex) => renderOption(option, `${option.value}-${index}-${optionIndex}`))}
                  </SelectPrimitive.Group>
                );
              })
            )}
          </SelectPrimitive.Viewport>
          <SelectPrimitive.ScrollDownButton data-slot="select-scroll-down-button" className="flex cursor-default items-center justify-center py-1">
            <ChevronDown className="size-4" />
          </SelectPrimitive.ScrollDownButton>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
});
