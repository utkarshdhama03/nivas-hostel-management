import { Moon, Sun } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'

/** Light and dark mode are one switch: the "dark" class on <html> (src/index.css). */
export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label="Toggle dark mode"
      onClick={() => {
        document.documentElement.classList.toggle('dark', !dark)
        setDark(!dark)
      }}
    >
      {dark ? <Sun size={16} /> : <Moon size={16} />}
    </Button>
  )
}
