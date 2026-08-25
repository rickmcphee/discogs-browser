// The artist labels the API returns are canonical, not raw stored strings:
// db.canonical_artist_labels picks one casing per artist from live row counts,
// preferring the Discogs catalog's. So the label for a selected artist can
// change under us -- a stock sync replaces one crawler's rows at a time, and a
// collection sync can bring an artist into `catalog` for the first time, either
// of which can flip "Jets to Brazil" to "Jets To Brazil" between refetches.
//
// The sidebar highlight is an exact string comparison, so a flipped label would
// leave nothing highlighted while `artist=` kept filtering -- the invisible
// filter changeFilter() already goes out of its way to avoid.
//
// Returns the label that should stay selected: the same string when the list
// still offers it, its re-cased equivalent when only the casing moved, or ''
// when the artist is gone from the list entirely.
//
// The re-casing match is a JS fold, and JS is not the authority on which labels
// are one artist -- Postgres LOWER() is (backend/db.py canonical_artist_labels).
// The two can disagree in both directions, so the match additionally requires
// equal length, which is what a pure change of case looks like. That rules out
// the disagreement that matters: JS folds precomposed "İsis" (U+0130, 4 chars)
// and decomposed "i̇sis" (i + U+0307, 5 chars) to the same string, while
// Postgres keeps them in separate groups, and without the length check a
// vanished "İsis" would silently hand the filter to the other group.
//
// No JS-side rule can be airtight -- e.g. JS and glibc could disagree about the
// Kelvin sign U+212A folding to "k" -- so the residual risk is a selection
// that follows a same-length label Postgres considers a different artist. The
// cost is a mislabelled highlight over the wrong rows, one click from correct.
// Eliminating it means the API returning a database-derived folded identity
// alongside each label; see the design doc for why that isn't worth the
// schema and contract churn here.
export function reconcileSelectedArtist(artists: string[], selected: string): string {
  if (!selected || artists.includes(selected)) return selected
  const folded = selected.toLowerCase()
  return artists.find((a) => a.length === selected.length && a.toLowerCase() === folded) ?? ''
}
