import type { StockSourceCount } from '../api/types'

// The documented categorical palette's dark steps, in its fixed slot order,
// validated as a set against this panel's bg-gray-900 surface (lightness band,
// chroma floor, adjacent CVD separation, normal-vision separation, and >= 3:1
// contrast all pass). The order is the colourblind-safety mechanism, so slots
// are assigned in sequence and never cycled.
const SOURCE_COLORS = [
  '#3987e5', '#d95926', '#199e70', '#c98500',
  '#d55181', '#008300', '#9085e9', '#e66767',
]

// Deliberately desaturated so the folded tail reads as "no single identity"
// rather than as a ninth store. Still clears 3:1 on the panel surface.
export const OTHER_COLOR = '#6b7280'

export const OTHER_KEY = '__other__'

export interface Slice {
  key: string
  label: string
  value: number
  color: string
}

// A store tab can carry more sources than the palette has slots, and a hue
// generated past the eighth would defeat the ordering that makes the set
// safe -- so the tail folds into one neutral wedge. Nothing is hidden by
// that: the legend below the ring lists every source with its own count,
// which is also what keeps identity off colour alone.
export function toSlices(sources: StockSourceCount[]): Slice[] {
  if (sources.length <= SOURCE_COLORS.length) {
    return sources.map((s, i) => ({
      key: String(s.crawler_id), label: s.site_name, value: s.count, color: SOURCE_COLORS[i],
    }))
  }
  const named = sources.slice(0, SOURCE_COLORS.length - 1)
  const rest = sources.slice(SOURCE_COLORS.length - 1)
  return [
    ...named.map((s, i) => ({
      key: String(s.crawler_id), label: s.site_name, value: s.count, color: SOURCE_COLORS[i],
    })),
    {
      key: OTHER_KEY,
      label: `Other (${rest.length} sources)`,
      value: rest.reduce((sum, s) => sum + s.count, 0),
      color: OTHER_COLOR,
    },
  ]
}
