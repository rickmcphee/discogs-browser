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
// "Re-casing" is really "re-spelling": the label also carries whichever
// punctuation the winning row was stored with, and the backend groups an
// artist across the variants db.py's _artist_punct_fold folds together -- "&"
// against "and", "-" against " ". So the label can flip from "Blink-182" to
// "Blink 182", or "Hall & Oates" to "Hall and Oates", with the group beneath it
// unchanged. labelFold below reproduces the backend's grouping key as far as a
// string transform can carry it, and both sides go through it before they are
// compared, so those flips are followed rather than treated as the artist
// vanishing.
//
// It has to carry the article fold as well as the punctuation one, because the
// two compose in the backend and not in the label. "The-Beatles" and "The
// Beatles" are one group there (the hyphen becomes a space before the article
// is stripped), but the winner's label is formatted by _the_comma_form_sql,
// whose own `LIKE 'the %'` guard the hyphen defeats -- so that group displays
// as "The-Beatles" while the hyphen spelling leads the vote and as "Beatles,
// The" once the spaced one does. Punctuation folding alone leaves those two
// unequal.
//
// The re-casing match is a JS fold, and JS is not the authority on which labels
// are one artist -- Postgres LOWER() is (backend/db.py canonical_artist_labels).
// The two can disagree in both directions, so the match additionally requires
// equal length *after* that punctuation fold, which is what a pure change of
// case looks like once spelling is accounted for. That rules out
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
function punctFold(name: string): string {
  return name.replace(/&/g, ' and ').replace(/-/g, ' ').replace(/\s+/g, ' ').trim()
}

// db.py's grouping key without the lowercasing -- _artist_punct_fold, then
// _the_comma_form_sql's "The X" -> "X, The" -- so the comparison below asks
// what the backend asks, rather than something adjacent to it. Case stays for
// the length guard to read.
function labelFold(name: string): string {
  const folded = punctFold(name)
  return /^the /i.test(folded) ? `${folded.slice(4)}, The` : folded
}

export function reconcileSelectedArtist(artists: string[], selected: string): string {
  if (!selected || artists.includes(selected)) return selected
  const foldedSelected = labelFold(selected)
  const lowered = foldedSelected.toLowerCase()
  return artists.find((a) => {
    const folded = labelFold(a)
    return folded.length === foldedSelected.length && folded.toLowerCase() === lowered
  }) ?? ''
}
