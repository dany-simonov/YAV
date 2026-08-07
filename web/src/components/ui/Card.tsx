/**
 * Card Component
 * ==============
 * Контейнер для группировки контента.
 */

import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '../../lib/utils';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'elevated' | 'outlined';
  padding?: 'none' | 'sm' | 'md' | 'lg';
}

const variantClasses = {
  default: 'bg-mv-surface border border-mv-border',
  elevated: 'bg-mv-surface border border-mv-border shadow-[0_1px_2px_rgba(0,0,0,.05),0_16px_40px_rgba(0,0,0,.07)]',
  outlined: 'bg-transparent border border-mv-border',
};

const paddingClasses = {
  none: '',
  sm: 'p-4',
  md: 'p-6',
  lg: 'p-8',
};

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ variant = 'default', padding = 'md', className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          'rounded-xl',
          variantClasses[variant],
          paddingClasses[padding],
          className
        )}
        {...props}
      >
        {children}
      </div>
    );
  }
);

Card.displayName = 'Card';

// ============================================================================
// Card Header
// ============================================================================

interface CardHeaderProps extends HTMLAttributes<HTMLDivElement> {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}

export function CardHeader({ title, description, action, icon, className, ...props }: CardHeaderProps) {
  return (
    <div className={cn('flex items-start justify-between mb-6', className)} {...props}>
      <div>
        <h3 className="text-lg font-semibold text-mv-text flex items-center gap-2">
          {icon && <span className="flex-shrink-0">{icon}</span>}
          <span>{title}</span>
        </h3>
        {description && (
          <p className="mt-1 text-sm text-mv-text-secondary">{description}</p>
        )}
      </div>
      {action && <div>{action}</div>}
    </div>
  );
}
