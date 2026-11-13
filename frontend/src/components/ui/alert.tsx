import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

export function Alert({ className, variant = 'info', ...props }: ComponentProps<'div'> & { variant?: 'info' | 'error' | 'success' }) {
  return (
    <div
      role={variant === 'error' ? 'alert' : 'status'}
      className={cn(
        'rounded-lg border px-4 py-3 text-sm',
        variant === 'error' && 'border-destructive/40 bg-destructive/10 text-destructive',
        variant === 'success' && 'border-success/40 bg-success/10',
        variant === 'info' && 'border-border bg-muted',
        className,
      )}
      {...props}
    />
  )
}
