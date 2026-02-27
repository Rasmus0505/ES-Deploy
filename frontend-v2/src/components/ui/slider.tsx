import * as SliderPrimitive from '@radix-ui/react-slider';
import { forwardRef, useMemo, type ComponentPropsWithoutRef, type ElementRef } from 'react';
import { cn } from '../../lib/utils';
import './slider.css';

type SliderProps = ComponentPropsWithoutRef<typeof SliderPrimitive.Root>;

export const Slider = forwardRef<ElementRef<typeof SliderPrimitive.Root>, SliderProps>(
  function Slider({ className, value, defaultValue, min = 0, ...props }, ref) {
    const values = useMemo(
      () => (Array.isArray(value) ? value : Array.isArray(defaultValue) ? defaultValue : [min]),
      [defaultValue, min, value]
    );

    return (
      <SliderPrimitive.Root ref={ref} value={value} defaultValue={defaultValue} min={min} className={cn('ui-slider', className)} {...props}>
        <SliderPrimitive.Track className="ui-slider__track">
          <SliderPrimitive.Range className="ui-slider__range" />
        </SliderPrimitive.Track>
        {Array.from({ length: values.length }, (_, index) => (
          <SliderPrimitive.Thumb key={index} className="ui-slider__thumb" />
        ))}
      </SliderPrimitive.Root>
    );
  }
);
