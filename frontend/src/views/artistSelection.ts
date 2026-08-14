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
// when the artist is gone from the list entirely. That last case also covers
// JS's toLowerCase disagreeing with Postgres LOWER() for some name (they are
// not the same function): the selection resets visibly to "All" rather than
// silently mismatching.
export function reconcileSelectedArtist(artists: string[], selected: string): string {
  if (!selected || artists.includes(selected)) return selected
  const folded = selected.toLowerCase()
  return artists.find((a) => a.toLowerCase() === folded) ?? ''
}
