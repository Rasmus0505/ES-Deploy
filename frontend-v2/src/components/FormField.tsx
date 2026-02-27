import type { InputHTMLAttributes } from 'react';

export function FormField({ label, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="v2-form-field">
      <span>{label}</span>
      <input {...props} />
    </label>
  );
}
