import { useEffect, useState } from 'react'
import { avatarUrl } from '../api/client'

interface Props {
  version: number
  size: 'sm' | 'lg'
}

const SIZE_CLASSES: Record<Props['size'], string> = {
  sm: 'w-8 h-8',
  lg: 'w-24 h-24',
}

export default function Avatar({ version, size }: Props) {
  const [broken, setBroken] = useState(false)

  useEffect(() => {
    setBroken(false)
  }, [version])

  if (version === 0 || broken) {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={`${SIZE_CLASSES[size]} text-gray-400`}>
        <circle cx="12" cy="12" r="11" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="12" cy="9.5" r="3.25" stroke="currentColor" strokeWidth="1.5" />
        <path d="M5.5 19c1.4-2.7 3.9-4 6.5-4s5.1 1.3 6.5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    )
  }

  return (
    <img
      src={avatarUrl(version)}
      alt="Profile"
      onError={() => setBroken(true)}
      className={`${SIZE_CLASSES[size]} rounded-full object-cover`}
    />
  )
}
