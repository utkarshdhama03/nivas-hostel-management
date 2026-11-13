import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

export function Input({ className, ...props }: ComponentProps<'input'>) {
  return (
    <input
      className={cn(
        // focus-within, not focus-visible: Tab moves through a date field's segments and then its calendar
        // button inside the browser's own shadow DOM, where only :focus-within still matches (NFR-13).
        'flex h-10 w-full rounded-lg border border-input bg-card px-3 text-sm placeholder:text-muted-foreground focus:outline-none focus-within:ring-2 focus-within:ring-ring aria-[invalid=true]:border-destructive',
        className,
      )}
      {...props}
    />
  )
}
