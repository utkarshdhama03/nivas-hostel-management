import { createContext } from 'react'

/** True inside the public layout (sidebar + <main>), so a page's card doesn't add a second <main>. */
export const InPublicLayout = createContext(false)
