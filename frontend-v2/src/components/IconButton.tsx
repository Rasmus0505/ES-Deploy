import type { ButtonHTMLAttributes, ReactNode } from 'react';

export function IconButton({ icon, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: ReactNode }) {
  return (
    <button className="v2-icon-button" type="button" {...props}>
      {icon}
      <span>{children}</span>
    </button>
  );
}
