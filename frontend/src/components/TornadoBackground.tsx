interface Hoop {
  cy: number
  rx: number
  ry: number
  rotate: number
}

interface VinylRecord {
  cx: number
  cy: number
  r: number
  rotate: number
}

const CENTER_X = 300

const HOOPS: Hoop[] = [
  { cy: 860, rx: 210, ry: 42, rotate: -4 },
  { cy: 800, rx: 185, ry: 40, rotate: 6 },
  { cy: 740, rx: 160, ry: 38, rotate: -7 },
  { cy: 680, rx: 135, ry: 34, rotate: 8 },
  { cy: 620, rx: 112, ry: 30, rotate: -9 },
  { cy: 560, rx: 90, ry: 26, rotate: 10 },
  { cy: 505, rx: 70, ry: 22, rotate: -11 },
  { cy: 455, rx: 52, ry: 18, rotate: 12 },
  { cy: 410, rx: 36, ry: 14, rotate: -13 },
  { cy: 370, rx: 22, ry: 10, rotate: 14 },
]

const RECORDS: VinylRecord[] = [
  { cx: 120, cy: 780, r: 55, rotate: -15 },
  { cx: 460, cy: 700, r: 45, rotate: 20 },
  { cx: 340, cy: 560, r: 38, rotate: -25 },
  { cx: 180, cy: 480, r: 42, rotate: 30 },
  { cx: 420, cy: 380, r: 30, rotate: -10 },
  { cx: 250, cy: 260, r: 26, rotate: 15 },
  { cx: 470, cy: 180, r: 34, rotate: -35 },
]

function RecordGlyph({ cx, cy, r, rotate }: VinylRecord) {
  return (
    <g transform={`rotate(${rotate} ${cx} ${cy})`}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="currentColor" strokeWidth={1.5} />
      <circle cx={cx} cy={cy} r={r * 0.65} fill="none" stroke="currentColor" strokeWidth={1} />
      <circle cx={cx} cy={cy} r={r * 0.4} fill="none" stroke="currentColor" strokeWidth={1} />
      <circle cx={cx} cy={cy} r={r * 0.12} fill="none" stroke="currentColor" strokeWidth={1} />
    </g>
  )
}

export default function TornadoBackground() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 600 900"
      preserveAspectRatio="xMidYMid slice"
      className="w-full h-full"
    >
      {HOOPS.map((hoop, i) => (
        <ellipse
          key={i}
          cx={CENTER_X}
          cy={hoop.cy}
          rx={hoop.rx}
          ry={hoop.ry}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          transform={`rotate(${hoop.rotate} ${CENTER_X} ${hoop.cy})`}
        />
      ))}
      {RECORDS.map((record, i) => (
        <RecordGlyph key={i} {...record} />
      ))}
    </svg>
  )
}
