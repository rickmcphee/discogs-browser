export interface DonutSegment<K extends string> {
  key: K
  value: number
  color: string
  /** Native tooltip for the arc — the hover layer, on both use sites. */
  title: string
}

interface Props<K extends string> {
  segments: DonutSegment<K>[]
  centreValue: string
  centreLabel: string
  ariaLabel: string
  /** Drawn thicker: a selection in the queue view, a hover in the store one. */
  emphasised?: K | null
  onSelect?: (key: K) => void
  onHover?: (key: K | null) => void
  className?: string
}

// Hand-rolled because the frontend has no charting dependency and this needs
// no more than stroke-dasharray on concentric arcs of one circle. Segments are
// drawn in the order given; the caller owns both that order and the colours,
// since what the ring encodes differs per use (three states of one quantity in
// the queue view, store identity in the store one).
export default function Donut<K extends string>({
  segments, centreValue, centreLabel, ariaLabel,
  emphasised = null, onSelect, onHover, className = 'w-40 h-40 shrink-0',
}: Props<K>) {
  const total = segments.reduce((sum, s) => sum + s.value, 0)
  const R = 60
  const C = 2 * Math.PI * R
  let offset = 0
  return (
    <svg viewBox="0 0 160 160" className={className} role="img" aria-label={ariaLabel}>
      <circle cx="80" cy="80" r={R} fill="none" stroke="#1f2937" strokeWidth="14" />
      {total > 0 && segments.filter((s) => s.value > 0).map((s) => {
        const length = (s.value / total) * C
        // A 2px gap between fills, per the mark spec, so adjacent segments read
        // as separate rather than as one continuous band -- but never more
        // circumference than the segment's own share: `offset` advances by
        // `length`, so a floor above it would paint over the next wedge and
        // overstate a sliver (1 of 5,000 is allocated 0.08 and would be drawn
        // at the 0.5 floor). Below the floor a segment gives up its gap
        // instead, which is the honest end of that trade.
        const drawn = Math.min(Math.max(length - 2, 0.5), length)
        const dash = `${drawn} ${C - drawn}`
        const dashOffset = -offset
        offset += length
        return (
          <circle
            key={s.key}
            cx="80" cy="80" r={R} fill="none"
            stroke={s.color}
            strokeWidth={emphasised === s.key ? 18 : 14}
            strokeDasharray={dash}
            strokeDashoffset={dashOffset}
            transform="rotate(-90 80 80)"
            className={onSelect ? 'cursor-pointer' : undefined}
            onClick={onSelect ? () => onSelect(s.key) : undefined}
            onMouseEnter={onHover ? () => onHover(s.key) : undefined}
            onMouseLeave={onHover ? () => onHover(null) : undefined}
          >
            <title>{s.title}</title>
          </circle>
        )
      })}
      <text x="80" y="76" textAnchor="middle" className="fill-gray-100" style={{ fontSize: 22 }}>{centreValue}</text>
      <text x="80" y="94" textAnchor="middle" className="fill-gray-400" style={{ fontSize: 10 }}>{centreLabel}</text>
    </svg>
  )
}
