import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Combine class names, letting later Tailwind classes override earlier ones.
 * cn('px-2', isActive && 'bg-slate-900', 'px-4') -> 'bg-slate-900 px-4'
 * Every shadcn/ui component uses this (Q53).
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
