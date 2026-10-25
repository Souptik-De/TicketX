import { memo, useCallback, useMemo, useRef } from "react";

/**
 * The shared seat map.
 *
 * Presentational by design: it renders whatever `tiers` it is given and reports
 * a click through `onToggle`. It knows nothing about holds, prices or the API,
 * which is what lets the admin builder preview reuse it in read-only mode.
 *
 * Layouts can reach 2000 seats, so the seat and row components are memoised and
 * keyed by seat id. `onToggle` is kept in a ref and handed down as a stable
 * wrapper: a parent that passes an inline arrow (everyone will) would otherwise
 * invalidate every memoised seat on each render.
 */

const EMPTY_SELECTION = [];

const STATE_LABELS = {
  available: "Available",
  held: "Held by someone else",
  booked: "Booked",
  blocked: "Blocked",
};

const LEGEND = [
  { key: "available", label: "Available" },
  { key: "selected", label: "Selected" },
  { key: "held", label: "Held by someone else" },
  { key: "booked", label: "Booked" },
  { key: "blocked", label: "Blocked" },
];

function isSelectable(seat, allowMineSelection) {
  return seat.state === "available" || (allowMineSelection && seat.mine && seat.state === "held");
}

function seatAppearance(seat, selected) {
  if (seat.state === "blocked") return "blocked";
  if (seat.state === "booked") return "booked";
  if (seat.state === "held") {
    if (seat.mine) return selected ? "selected" : "held-mine";
    return "held";
  }
  return selected ? "selected" : "available";
}

function seatStateLabel(appearance, seat) {
  if (appearance === "selected") return "Selected";
  if (appearance === "held-mine") return "Held by you";
  return STATE_LABELS[seat.state] ?? "Seat";
}

const SeatButton = memo(function SeatButton({ seat, tierName, selected, readOnly, allowMineSelection, onToggle }) {
  const appearance = seatAppearance(seat, selected);
  const stateLabel = seatStateLabel(appearance, seat);
  // A held, booked or blocked seat cannot be picked; read-only disables the rest.
  // A seat the caller already holds is selectable when the page allows replacing
  // an old hold: the server cancels that hold and claims the new set atomically.
  const disabled = readOnly || !isSelectable(seat, allowMineSelection);
  const description = `${seat.label}, ${tierName}, ${stateLabel}`;

  return (
    <button
      type="button"
      className={`seatmap-seat seatmap-seat--${appearance}`}
      disabled={disabled}
      aria-label={description}
      aria-pressed={selectablePressed(disabled, selected)}
      title={description}
      onClick={disabled ? undefined : () => onToggle(seat)}
      data-seat-id={seat.id}
    >
      {seat.number}
    </button>
  );
});

// aria-pressed is only meaningful for a control that can be toggled.
function selectablePressed(disabled, selected) {
  return disabled ? undefined : selected;
}

const SeatRow = memo(function SeatRow({ tierName, row, selectedIds, readOnly, allowMineSelection, onToggle }) {
  return (
    <div className="seatmap-row">
      <span className="seatmap-row-label" aria-hidden="true">
        {row.label}
      </span>
      <div className="seatmap-row-blocks" role="group" aria-label={`Row ${row.label}`}>
        {row.blocks.map((block, blockIndex) => (
          // The gap between blocks is the aisle; an empty span would be read out.
          <div className="seatmap-block" key={`${row.label}-${blockIndex}`}>
            {block.map((seat) => (
              <SeatButton
                key={seat.id}
                seat={seat}
                tierName={tierName}
                selected={selectedIds.has(seat.id)}
                readOnly={readOnly}
                allowMineSelection={allowMineSelection}
                onToggle={onToggle}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
});

export function SeatMap({
  tiers,
  selectedIds = EMPTY_SELECTION,
  onToggle,
  readOnly = false,
  allowMineSelection = false,
}) {
  const selected = useMemo(() => new Set(selectedIds ?? EMPTY_SELECTION), [selectedIds]);

  const onToggleRef = useRef(onToggle);
  onToggleRef.current = onToggle;
  const handleToggle = useCallback((seat) => {
    onToggleRef.current?.(seat);
  }, []);

  const counts = useMemo(() => {
    let available = 0;
    let held = 0;
    let booked = 0;
    let hasMine = false;

    for (const tier of tiers ?? []) {
      for (const row of tier.rows ?? []) {
        for (const block of row.blocks ?? []) {
          for (const seat of block) {
            if (seat.state === "available") {
              available += 1;
            } else if (seat.state === "held") {
              held += 1;
              if (seat.mine) hasMine = true;
            } else if (seat.state === "booked") {
              booked += 1;
            }
          }
        }
      }
    }

    // Blocked seats are not part of capacity, matching the API's own totals.
    return { available, held, booked, total: available + held + booked, hasMine };
  }, [tiers]);

  const hasSeats = (tiers ?? []).some((tier) => (tier.rows ?? []).some((row) => (row.blocks ?? []).some((block) => block.length > 0)));

  if (!hasSeats) {
    return <p className="muted-text">No seats have been laid out for this event yet.</p>;
  }

  return (
    <div className="seatmap">
      <div className="seatmap-legend" aria-label="Seat map legend">
        {LEGEND.map((item) => (
          <span className="seatmap-legend-item" key={item.key}>
            <span className={`seatmap-swatch seatmap-seat--${item.key}`} aria-hidden="true" />
            {item.label}
          </span>
        ))}
        {counts.hasMine && (
          <span className="seatmap-legend-item">
            <span className="seatmap-swatch seatmap-seat--held-mine" aria-hidden="true" />
            Held by you
          </span>
        )}
      </div>

      <div className="seatmap-scroll">
        <div className="seatmap-inner">
          <div className="seatmap-stage" aria-hidden="true">
            STAGE
          </div>

          {(tiers ?? []).map((tier, tierIndex) => (
            <section className="seatmap-tier" key={`${tier.name}-${tierIndex}`}>
              <h3 className="seatmap-tier-name">{tier.name}</h3>
              <div className="seatmap-rows">
                {(tier.rows ?? []).map((row, rowIndex) => (
                  <SeatRow
                    key={`${row.label}-${rowIndex}`}
                    tierName={tier.name}
                    row={row}
                    selectedIds={selected}
                    readOnly={readOnly}
                    allowMineSelection={allowMineSelection}
                    onToggle={handleToggle}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>

      <p className="seatmap-counts">
        <span>
          Available <strong>{counts.available}</strong>
        </span>
        <span>
          Held <strong>{counts.held}</strong>
        </span>
        <span>
          Booked <strong>{counts.booked}</strong>
        </span>
        <span>
          Total <strong>{counts.total}</strong>
        </span>
        {!readOnly && (
          <span className="seatmap-selected-count">
            Selected <strong>{selected.size}</strong>
          </span>
        )}
      </p>
    </div>
  );
}
