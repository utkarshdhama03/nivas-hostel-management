// The favicon and app icons: index.html links them, the files are there, and the manifest names them.
import { expect, test } from 'vitest'

import indexHtml from '../index.html?raw'
import manifestText from '../public/site.webmanifest?raw'

const publicFiles = Object.keys(import.meta.glob('../public/*', { query: '?url', import: 'default' })).map((p) => p.replace('../public', ''))

test('index.html links the favicon, the touch icon and the manifest, and every one exists', () => {
  for (const [rel, href] of [
    ['icon', '/favicon.svg'],
    ['apple-touch-icon', '/apple-touch-icon.png'],
    ['manifest', '/site.webmanifest'],
  ]) {
    expect(indexHtml).toContain(`rel="${rel}" href="${href}"`)
    expect(publicFiles).toContain(href)
  }
})

test('the manifest names the app and its icons, and they exist', () => {
  const manifest = JSON.parse(manifestText) as { short_name: string; icons: { src: string }[] }
  expect(manifest.short_name).toBe('Nivas')
  for (const icon of manifest.icons) expect(publicFiles).toContain(icon.src)
})
