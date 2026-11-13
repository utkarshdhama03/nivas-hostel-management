// Original illustrations for the public pages, drawn for this project (docs/credits.md). Inline SVG,
// so they work offline and follow light/dark mode: every colour is a design token (fill-primary ...).
// The free photo sites (Unsplash, Pexels) refuse automated downloads, so these replace photos.

/** A hostel block with lit windows, trees, and a student with a travel bag on the path (D75: no gate). */
export function HostelIllustration({ className }: { className?: string }) {
  const windows: [number, number][] = []
  for (let row = 0; row < 4; row++) for (let col = 0; col < 6; col++) windows.push([138 + col * 34, 62 + row * 34])
  const lit = new Set([1, 4, 8, 9, 13, 18, 22])
  return (
    <svg viewBox="0 0 480 320" role="img" aria-label="Illustration: a hostel building with a student carrying a travel bag" className={className}>
      <rect x="0" y="0" width="480" height="320" rx="16" className="fill-muted" />
      <circle cx="404" cy="58" r="24" className="fill-primary/25" />
      {/* the hostel block */}
      <rect x="120" y="40" width="226" height="196" rx="6" className="fill-card stroke-border" strokeWidth="2" />
      <rect x="112" y="30" width="242" height="16" rx="4" className="fill-primary" />
      {windows.map(([x, y], i) => (
        <rect key={`${x}-${y}`} x={x} y={y} width="20" height="22" rx="3" className={lit.has(i) ? 'fill-primary/70' : 'fill-primary/15'} />
      ))}
      <rect x="214" y="196" width="38" height="40" rx="4" className="fill-primary/80" />
      {/* the ground and the path */}
      <rect x="0" y="236" width="480" height="84" rx="0" className="fill-success/15" />
      <path d="M214 236 L252 236 L300 320 L170 320 Z" className="fill-card" />
      {/* a bench beside the path */}
      <rect x="318" y="252" width="62" height="7" rx="3" className="fill-foreground/50" />
      <rect x="324" y="259" width="5" height="16" className="fill-foreground/50" />
      <rect x="369" y="259" width="5" height="16" className="fill-foreground/50" />
      {/* trees */}
      <rect x="62" y="196" width="8" height="44" className="fill-foreground/40" />
      <circle cx="66" cy="184" r="28" className="fill-success/60" />
      <rect x="436" y="206" width="6" height="34" className="fill-foreground/40" />
      <circle cx="439" cy="196" r="20" className="fill-success/50" />
      {/* a student on the path, off home on leave with a travel bag */}
      <circle cx="268" cy="240" r="10" className="fill-foreground/70" />
      <rect x="258" y="252" width="20" height="36" rx="8" className="fill-primary" />
      <rect x="260" y="288" width="7" height="22" rx="3" className="fill-foreground/70" />
      <rect x="270" y="288" width="7" height="22" rx="3" className="fill-foreground/70" />
      <path d="M284 270 v-6 a5 5 0 0 1 10 0 v6" className="fill-none stroke-foreground/70" strokeWidth="2" />
      <rect x="280" y="270" width="18" height="24" rx="4" className="fill-success/70" />
    </svg>
  )
}

/** Three cards on a phone screen: room, fees, leave. For "Who it's for". */
export function PortalIllustration({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 240 200" role="img" aria-label="Illustration: a student's phone showing room, fees and leave" className={className}>
      <rect x="0" y="0" width="240" height="200" rx="16" className="fill-muted" />
      <rect x="78" y="16" width="84" height="168" rx="14" className="fill-card stroke-foreground/60" strokeWidth="3" />
      <rect x="108" y="24" width="24" height="4" rx="2" className="fill-foreground/40" />
      {[44, 88, 132].map((y, i) => (
        <g key={y}>
          <rect x="88" y={y} width="64" height="36" rx="6" className={i === 1 ? 'fill-primary/25' : 'fill-primary/10'} />
          <circle cx="102" cy={y + 18} r="7" className={i === 2 ? 'fill-success/70' : 'fill-primary/70'} />
          <rect x="114" y={y + 12} width="30" height="5" rx="2" className="fill-foreground/50" />
          <rect x="114" y={y + 21} width="20" height="4" rx="2" className="fill-foreground/30" />
        </g>
      ))}
      <circle cx="40" cy="150" r="18" className="fill-success/30" />
      <circle cx="204" cy="56" r="14" className="fill-primary/25" />
    </svg>
  )
}
