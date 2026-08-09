const INPUT_BASE =
  'rounded-full bg-gray-800 border text-white placeholder-gray-500 focus:outline-none focus:border-gray-400 transition-colors'

export function textInputClass(isInvalid = false): string {
  return `${INPUT_BASE} ${isInvalid ? 'border-red-500' : 'border-gray-700'}`
}

export function selectClass(): string {
  return `${INPUT_BASE} border-gray-700`
}
