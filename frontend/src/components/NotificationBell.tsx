interface Props {
  unread: number
  active: boolean
  onClick: () => void
}

// The dot is decorative and marked aria-hidden; the count goes into the
// button's label instead, so the state is never carried by colour alone.
export default function NotificationBell({ unread, active, onClick }: Props) {
  return (
    <button
      onClick={onClick}
      aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
      className={`relative w-11 h-11 md:w-8 md:h-8 flex items-center justify-center rounded-full transition-colors ${
        active ? 'bg-white text-gray-950' : 'text-gray-400 hover:text-white hover:bg-gray-800'
      }`}
    >
      <svg
        width="22" height="22" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M18 8a6 6 0 0 0-12 0c0 6-2 7-2 7h16s-2-1-2-7" />
        <path d="M10.3 20a2 2 0 0 0 3.4 0" />
      </svg>
      {unread > 0 && (
        <span
          aria-hidden="true"
          data-testid="notification-dot"
          className="absolute top-1.5 right-1.5 md:top-0.5 md:right-0.5 w-2.5 h-2.5 rounded-full bg-red-500 ring-2 ring-gray-900"
        />
      )}
    </button>
  )
}
