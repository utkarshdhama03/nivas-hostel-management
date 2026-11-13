// NFR-13: WCAG AA colour contrast in both themes, checked on the design tokens themselves, so a
// token change that breaks contrast fails the quality gate. Text needs 4.5:1; form-control
// borders and the focus ring need 3:1 (WCAG 1.4.3 and 1.4.11).
import { describe, expect, it } from 'vitest'

import css from './index.css?raw'

function tokens(selector: string): (name: string) => string {
  const start = css.indexOf(`${selector} {`)
  const body = css.slice(start, css.indexOf('}', start))
  const found = new Map([...body.matchAll(/--([a-z-]+):\s*oklch\(([^)]*)\)/g)].map((m) => [m[1], m[2]]))
  return (name) => {
    const v = found.get(name)
    if (v === undefined) throw new Error(`token --${name} not found in ${selector}`)
    return v
  }
}

/** Relative luminance of an OKLCH colour (via OKLab -> linear sRGB, clipped to the gamut). */
function luminance(oklch: string): number {
  const [L = 0, C = 0, h = 0] = oklch.trim().split(/\s+/).map(Number)
  const a = C * Math.cos((h * Math.PI) / 180)
  const b = C * Math.sin((h * Math.PI) / 180)
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
  const clip = (x: number) => Math.min(1, Math.max(0, x))
  const R = clip(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s)
  const G = clip(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s)
  const B = clip(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)
  return 0.2126 * R + 0.7152 * G + 0.0722 * B
}

function contrast(x: string, y: string): number {
  const [hi = 0, lo = 0] = [luminance(x), luminance(y)].sort((p, q) => q - p)
  return (hi + 0.05) / (lo + 0.05)
}

const SURFACES = ['background', 'card', 'muted']
const TEXT = ['foreground', 'muted-foreground', 'primary', 'destructive', 'success']

describe.each([
  ['light', ':root'],
  ['dark', '.dark'],
])('%s theme', (_name, selector) => {
  const t = tokens(selector)

  it.each(TEXT.flatMap((fg) => SURFACES.map((bg) => [fg, bg])))('text %s on %s is at least 4.5:1', (fg, bg) => {
    expect(contrast(t(fg), t(bg))).toBeGreaterThanOrEqual(4.5)
  })

  it.each([
    ['primary-foreground', 'primary'],
    ['destructive-foreground', 'destructive'],
  ])('button text %s on %s is at least 4.5:1', (fg, bg) => {
    expect(contrast(t(fg), t(bg))).toBeGreaterThanOrEqual(4.5)
  })

  it.each(SURFACES.flatMap((bg) => [['input', bg], ['ring', bg]]))('%s on %s is at least 3:1', (fg, bg) => {
    expect(contrast(t(fg), t(bg))).toBeGreaterThanOrEqual(3)
  })

  it('the checker itself: black on white is 21:1', () => {
    expect(contrast('0 0 0', '1 0 0')).toBeCloseTo(21, 0)
  })
})
