/**
 * Button Component
 * ================
 * Универсальная кнопка с различными вариантами стилизации.
 */

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '../../lib/utils';
import { Spinner } from './Spinner';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  fullWidth?: boolean;
}

const variantClasses = {
  primary: 'bg-mv-accent text-white hover:bg-mv-accent-hover shadow-sm hover:-translate-y-px active:translate-y-0',
  secondary: 'bg-mv-surface-2 text-mv-text hover:bg-mv-border active:bg-mv-border/80',
  ghost: 'bg-transparent text-mv-text-secondary hover:bg-mv-surface-2 hover:text-mv-text',
  danger: 'bg-mv-fake text-white hover:bg-mv-fake/90 active:bg-mv-fake/80',
  outline: 'bg-white border border-mv-border text-mv-text hover:bg-mv-surface-2',
};

const sizeClasses = {
  sm: 'px-3 py-1.5 text-sm',
  md: 'px-4 py-2.5 text-sm',
  lg: 'px-6 py-3 text-base',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      children,
      variant = 'primary',
      size = 'md',
      isLoading = false,
      leftIcon,
      rightIcon,
      fullWidth = false,
      disabled,
      className,
      ...props
    },
    ref
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || isLoading}
        className={cn(
          'inline-flex items-center justify-center gap-2 font-medium rounded-lg',
          'transition-all duration-200 ease-out',
          '[&_.lucide]:!text-current',
          'focus:outline-none focus:ring-2 focus:ring-mv-accent focus:ring-offset-2 focus:ring-offset-mv-bg shadow-sm',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          variantClasses[variant],
          sizeClasses[size],
          fullWidth && 'w-full',
          className
        )}
        {...props}
      >
        {isLoading ? (
          <>
            <Spinner size="sm" className="border-current border-t-transparent" />
            <span>Загрузка...</span>
          </>
        ) : (
          <>
            {leftIcon && <span className="flex-shrink-0">{leftIcon}</span>}
            {children}
            {rightIcon && <span className="flex-shrink-0">{rightIcon}</span>}
          </>
        )}
      </button>
    );
  }
);

Button.displayName = 'Button';
