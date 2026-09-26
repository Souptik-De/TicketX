export function TicketXLogo({ size = 28 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      role="img"
    >
      <defs>
        <linearGradient id="ticketx-logo-right" x1="32" y1="10" x2="32" y2="54" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#1da1f2" />
          <stop offset="0.55" stopColor="#4f46e5" />
          <stop offset="1" stopColor="#9333ea" />
        </linearGradient>
        <mask id="ticketx-logo-notch">
          <rect x="0" y="0" width="64" height="64" fill="white" />
          <circle cx="48.5" cy="32" r="5.4" fill="black" />
        </mask>
      </defs>
      <path
        d="M13 15h11.2c2.4 0 4.6 1.3 5.8 3.4l6.6 11.4c1.2 2.1 1.2 4.7 0 6.8L30 48c-1.2 2.1-3.4 3.4-5.8 3.4H13c-2.5 0-4-2.7-2.7-4.8L20.5 32 10.3 19.8c-1.3-2.1.2-4.8 2.7-4.8Z"
        fill="currentColor"
        opacity="0.92"
      />
      <g mask="url(#ticketx-logo-notch)">
        <path
          d="M40.8 15H51c2.5 0 4 2.7 2.7 4.8L45.6 32l8.1 12.2c1.3 2.1-.2 4.8-2.7 4.8H40.8c-2.4 0-4.6-1.3-5.8-3.4l-6.6-11.4c-1.2-2.1-1.2-4.7 0-6.8L34.9 18.4c1.3-2.1 3.5-3.4 5.9-3.4Z"
          fill="url(#ticketx-logo-right)"
        />
      </g>
    </svg>
  );
}
