import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function clamp(input: number, min: number, max: number) {
  if (!Number.isFinite(input)) return min;
  return Math.min(max, Math.max(min, input));
}

export function toNumber(input: unknown, fallback = 0) {
  const value = Number(input);
  return Number.isFinite(value) ? value : fallback;
}
