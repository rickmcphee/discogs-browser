// Stock rows carry the crawler's own `currency` (a pass-through string, set
// per-crawler), but the price cell hardcoded a `$` and ignored it -- so
// SPV Entertainment's EUR prices rendered as "$27.99". Every other source
// hardcodes "USD", which is why the bug stayed invisible until the first
// EU-domiciled store was added.
//
// A symbol map rather than Intl.NumberFormat: Intl would also start inserting
// thousands separators into USD prices, changing how every existing source
// renders to fix a bug in one of them. This keeps `toFixed(2)` exactly as it
// was, so USD output is byte-for-byte unchanged.
const SYMBOLS: Record<string, string> = {
  USD: '$',
  EUR: '€',
  GBP: '£',
  CAD: 'CA$',
  AUD: 'A$',
}

export function formatPrice(price: number, currency: string | null): string {
  // A null currency predates the column being populated and is USD in practice
  // (45 of the 46 sources hardcode it), so defaulting keeps those rows rendering
  // as they always have rather than regressing them to a bare number.
  const code = (currency ?? 'USD').toUpperCase()
  const symbol = SYMBOLS[code]
  // An unmapped-but-real code prints as "27.99 SEK" -- unambiguous, and better
  // than guessing a symbol or silently showing the wrong one.
  return symbol ? `${symbol}${price.toFixed(2)}` : `${price.toFixed(2)} ${code}`
}
