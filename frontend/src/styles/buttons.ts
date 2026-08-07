export function navButtonClass(isActive: boolean): string {
  const base = 'rounded-full transition-colors'
  return isActive
    ? `${base} bg-white text-gray-950`
    : `${base} text-gray-400 hover:text-white hover:bg-gray-800`
}

export function primaryButtonClass(): string {
  return 'rounded-full bg-white hover:bg-gray-200 active:bg-gray-300 text-gray-950 font-medium transition-colors'
}

export function secondaryButtonClass(): string {
  return 'rounded-full bg-gray-700 hover:bg-gray-600 text-white font-medium transition-colors'
}

export function dismissButtonClass(): string {
  return 'rounded-full hover:bg-gray-800 text-gray-400 hover:text-white transition-colors'
}
