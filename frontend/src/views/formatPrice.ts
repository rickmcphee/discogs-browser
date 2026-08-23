// Stock rows carry the crawler's own `currency`, but the price cell hardcoded a
// `$` and ignored it. This is a live bug, not a pre-emptive fix: Jetglow
// Recordings (Italian, Big Cartel) hardcodes EUR and darkdescentrecords.py
// passes its feed's currency through, so EUR rows are already being rendered as
// dollars today. SPV Entertainment adds another.
//
// A symbol map rather than Intl.NumberFormat: Intl would also start inserting
// thousands separators into USD prices, changing how every USD source renders
// in order to fix a bug in the non-USD ones. This keeps `toFixed(2)` exactly as
// it was, so USD output is byte-for-byte unchanged.
const SYMBOLS: Record<string, string> = {
  USD: '$',
  EUR: '€',
  GBP: '£',
  CAD: 'CA$',
  AUD: 'A$',
}

export function formatPrice(price: number, currency: string | null): string {
  // A null currency predates the column being populated, and the large majority
  // of sources hardcode USD, so defaulting keeps those rows rendering as they
  // always have rather than regressing them to a bare number.
  const code = (currency ?? 'USD').toUpperCase()
  const symbol = SYMBOLS[code]
  // An unmapped-but-real code prints as "27.99 SEK" -- unambiguous, and better
  // than guessing a symbol or silently showing the wrong one.
  return symbol ? `${symbol}${price.toFixed(2)}` : `${price.toFixed(2)} ${code}`
}
