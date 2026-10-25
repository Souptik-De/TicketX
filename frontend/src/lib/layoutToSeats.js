/**
 * Pure helpers behind the admin seat-layout builder.
 *
 * The shapes here mirror the backend's `SeatLayoutIn` rules exactly, so the
 * builder can show a problem before a round trip. The server is still the
 * authority: `validateLayout` is a convenience, not a second source of truth.
 *
 * Builder rows keep their text fields as the admin typed them (`blocks: "3,14,3"`)
 * because a half-typed value like "3," is normal while editing. Parsing and
 * serialising therefore happen at the edges: `layoutToSeats` for the preview,
 * `serializeLayout` for the POST body.
 */

export const MAX_TIERS = 10;
export const MAX_ROWS = 60;
export const MAX_SEATS = 2000;
export const MAX_ROW_LABEL_LENGTH = 24;

let nextClientId = 0;

function clientId(prefix) {
  nextClientId += 1;
  return `${prefix}-${nextClientId}`;
}

export function makeRow(overrides = {}) {
  return { id: clientId("row"), label: "", blocks: "10", blocked: "", ...overrides };
}

export function makeTier(overrides = {}) {
  return { id: clientId("tier"), name: "General", rows: [makeRow()], ...overrides };
}

export function emptyLayout() {
  // The first row is prefilled so switching to "Seat map" starts from a valid,
  // visible layout instead of a validation error the admin has to hunt down.
  return { tiers: [makeTier({ rows: [makeRow({ label: "A" })] })] };
}

function tokenizeCsv(value) {
  return String(value ?? "")
    .split(",")
    .map((token) => token.trim())
    .filter(Boolean);
}

/** `"3,14,3"` -> `{ values: [3, 14, 3], invalid: [] }`. Empty pieces are ignored. */
export function parseSeatNumbers(value) {
  const values = [];
  const invalid = [];
  for (const token of tokenizeCsv(value)) {
    if (/^\d+$/.test(token)) {
      const number = Number(token);
      if (Number.isSafeInteger(number) && number >= 1) {
        values.push(number);
        continue;
      }
    }
    invalid.push(token);
  }
  return { values, invalid };
}

export const parseBlocks = parseSeatNumbers;
export const parseBlocked = parseSeatNumbers;

/** Every row in the layout, in the order the map will render them. */
export function layoutRows(layout) {
  return (layout?.tiers ?? []).flatMap((tier) => (tier.rows ?? []).map((row) => ({ tier, row })));
}

export function usedRowLabels(layout) {
  const used = new Set();
  for (const { row } of layoutRows(layout)) {
    const label = String(row.label ?? "").trim();
    if (label) used.add(label.toLowerCase());
  }
  return used;
}

const LETTERS_ONLY = /^[A-Za-z]+$/;

/** 0 -> A, 25 -> Z, 26 -> AA, 27 -> AB ... (60 rows needs past Z). */
export function columnLabel(index) {
  let remaining = index;
  let label = "";
  while (remaining >= 0) {
    label = String.fromCharCode(65 + (remaining % 26)) + label;
    remaining = Math.floor(remaining / 26) - 1;
  }
  return label;
}

/** "A" -> 0, "Z" -> 25, "AA" -> 26. Null when not a letter sequence. */
export function labelIndex(label) {
  if (!LETTERS_ONLY.test(label ?? "")) return null;
  let index = 0;
  for (const character of label.toUpperCase()) {
    index = index * 26 + (character.charCodeAt(0) - 64);
  }
  return index - 1;
}

function firstFreeLabel(startIndex, used) {
  let index = Math.max(startIndex, 0);
  while (used.has(columnLabel(index).toLowerCase())) {
    index += 1;
  }
  return columnLabel(index);
}

/**
 * The label the quick-add field is prefilled with: continue after the last row
 * in the event, skipping anything already used. A last label that is not a plain
 * letter sequence ("VIP1") gives no usable position, so the search starts at A.
 */
export function nextRowLabel(layout) {
  const rows = layoutRows(layout);
  const used = usedRowLabels(layout);
  if (rows.length === 0) return firstFreeLabel(0, used);

  const lastLabel = String(rows[rows.length - 1].row.label ?? "").trim();
  const lastIndex = labelIndex(lastLabel);
  return firstFreeLabel(lastIndex === null ? 0 : lastIndex + 1, used);
}

/**
 * Append `count` rows to one tier.
 *
 * Honours the admin's start label, but never duplicates: a used label (or one
 * that gets used by an earlier row in the same batch) is skipped forward.
 * Nothing is added at all when the batch would break a rule, so a rejected
 * quick-add cannot leave half a batch behind.
 */
export function quickAddRows(layout, tierId, { start, count, blocks }) {
  const tiers = layout?.tiers ?? [];
  if (!tiers.some((tier) => tier.id === tierId)) {
    return { error: "Choose a tier to add rows to." };
  }

  const rowCount = Number(count);
  if (!Number.isInteger(rowCount) || rowCount < 1 || rowCount > MAX_ROWS) {
    return { error: `Enter a row count between 1 and ${MAX_ROWS}.` };
  }

  const rowsNow = layoutRows(layout).length;
  if (rowsNow + rowCount > MAX_ROWS) {
    const left = Math.max(MAX_ROWS - rowsNow, 0);
    return { error: `An event may have at most ${MAX_ROWS} rows; ${left} left to add.` };
  }

  const parsedBlocks = parseBlocks(blocks);
  if (parsedBlocks.values.length === 0 || parsedBlocks.invalid.length > 0) {
    return { error: "Blocks must be whole numbers of at least 1 (for example 3,14,3)." };
  }

  const startLabel = String(start ?? "").trim();
  const startIndex = labelIndex(startLabel);
  if (startIndex === null) {
    return { error: "Start label must be letters only (A, B, ... Z, AA)." };
  }

  const used = usedRowLabels(layout);
  const labels = [];
  let index = startIndex;
  while (labels.length < rowCount) {
    const candidate = columnLabel(index);
    if (!used.has(candidate.toLowerCase())) {
      used.add(candidate.toLowerCase());
      labels.push(candidate);
    }
    index += 1;
  }

  const blockText = parsedBlocks.values.join(",");
  const newRows = labels.map((label) => makeRow({ label, blocks: blockText }));
  return {
    layout: {
      ...layout,
      tiers: tiers.map((tier) =>
        tier.id === tierId ? { ...tier, rows: [...(tier.rows ?? []), ...newRows] } : tier,
      ),
    },
    added: labels,
    error: "",
  };
}

/**
 * The layout as the seat map renders it, with every seat "available" except the
 * blocked numbers. Blocked numbers follow the same continuous 1..sum(blocks)
 * numbering as the backend, and seat ids are the labels (unique in a valid
 * layout; a duplicate gets a suffix so React keys never collide mid-edit).
 */
export function layoutToSeats(layout) {
  const tiers = Array.isArray(layout?.tiers) ? layout.tiers : [];
  const labelCounts = new Map();
  let total = 0;
  let blocked = 0;

  // A single stray block of 100000 must not build 100000 buttons. The number is
  // still reported in the totals line so the validation message makes sense.
  const requested = tiers.reduce(
    (sum, tier) =>
      sum + (tier.rows ?? []).reduce(
        (rowSum, row) => rowSum + parseBlocks(row.blocks).values.reduce((a, b) => a + b, 0),
        0,
      ),
    0,
  );

  if (requested > MAX_SEATS) {
    return { tiers: [], totals: { total: requested, blocked: 0, sellable: requested }, hidden: true };
  }

  const renderedTiers = tiers.map((tier) => {
    const name = String(tier.name ?? "").trim() || "Seats";
    const rows = (tier.rows ?? []).map((row) => {
      const label = String(row.label ?? "").trim();
      const blockSizes = parseBlocks(row.blocks).values;
      const seatCount = blockSizes.reduce((sum, size) => sum + size, 0);
      const blockedNumbers = new Set(parseBlocked(row.blocked).values.filter((n) => n <= seatCount));

      let seatNumber = 0;
      const blocks = blockSizes.map((size) =>
        Array.from({ length: size }, () => {
          seatNumber += 1;
          total += 1;
          const seatLabel = `${label}${seatNumber}`;
          const seen = (labelCounts.get(seatLabel) ?? 0) + 1;
          labelCounts.set(seatLabel, seen);
          const isBlocked = blockedNumbers.has(seatNumber);
          if (isBlocked) blocked += 1;
          return {
            id: seen === 1 ? seatLabel : `${seatLabel}#${seen}`,
            number: seatNumber,
            label: seatLabel,
            state: isBlocked ? "blocked" : "available",
            mine: false,
          };
        }),
      );
      return { label, blocks };
    });
    return { name, rows };
  });

  return {
    tiers: renderedTiers,
    totals: { total, blocked, sellable: total - blocked },
    hidden: false,
  };
}

/** The POST body for `create_event`. Capacity is deliberately omitted. */
export function serializeLayout(layout) {
  return {
    tiers: (layout?.tiers ?? []).map((tier) => ({
      name: String(tier.name ?? "").trim(),
      rows: (tier.rows ?? []).map((row) => {
        const entry = {
          label: String(row.label ?? "").trim(),
          blocks: parseBlocks(row.blocks).values,
        };
        const blocked = parseBlocked(row.blocked).values;
        if (blocked.length > 0) entry.blocked = blocked;
        return entry;
      }),
    })),
  };
}

/**
 * Client-side mirror of the backend layout rules. Errors carry the tier/row id
 * they belong to so the builder can put the message next to the right field;
 * errors without either are event-wide and are listed above the form.
 */
export function validateLayout(layout) {
  const tiers = Array.isArray(layout?.tiers) ? layout.tiers : [];
  const errors = [];
  const add = (message, target = {}) => errors.push({ message, ...target });

  if (tiers.length < 1) add("Add at least one tier.");
  if (tiers.length > MAX_TIERS) add(`An event may have at most ${MAX_TIERS} tiers.`);

  const seenTierNames = new Set();
  const seenRowLabels = new Set();
  const generatedLabels = new Set();
  let rowCount = 0;
  let totalSeats = 0;

  for (const tier of tiers) {
    const name = String(tier.name ?? "").trim();
    if (!name) {
      add("Tier name is required.", { tierId: tier.id });
    } else {
      const folded = name.toLowerCase();
      if (seenTierNames.has(folded)) {
        add(`Tier name "${name}" is used more than once.`, { tierId: tier.id });
      }
      seenTierNames.add(folded);
    }

    const rows = Array.isArray(tier.rows) ? tier.rows : [];
    if (rows.length === 0) add("Add at least one row.", { tierId: tier.id });

    for (const row of rows) {
      rowCount += 1;
      const target = { tierId: tier.id, rowId: row.id };
      const label = String(row.label ?? "").trim();

      if (!label) {
        add("Row label is required.", target);
      } else if (label.length > MAX_ROW_LABEL_LENGTH) {
        add(`Row labels may be at most ${MAX_ROW_LABEL_LENGTH} characters.`, target);
      } else if (!/^[A-Za-z0-9]+$/.test(label)) {
        add("Row labels may contain only letters and digits.", target);
      } else {
        const folded = label.toLowerCase();
        if (seenRowLabels.has(folded)) {
          add(`Row label "${label}" is used more than once.`, target);
        }
        seenRowLabels.add(folded);
      }

      const blocks = parseBlocks(row.blocks);
      if (blocks.values.length === 0 || blocks.invalid.length > 0) {
        add("Blocks must be whole numbers of at least 1 (for example 3,14,3).", target);
      }
      const seatCount = blocks.values.reduce((sum, size) => sum + size, 0);
      totalSeats += seatCount;

      const blocked = parseBlocked(row.blocked);
      if (blocked.invalid.length > 0) {
        add("Blocked seats must be whole numbers of at least 1.", target);
      } else if (seatCount > 0) {
        const outside = blocked.values.find((number) => number > seatCount);
        if (outside !== undefined) {
          add(`Blocked seat ${outside} is outside 1..${seatCount} for row "${label || "?"}".`, target);
        }
      }

      // "A1" seat 2 and "A" seat 12 both render as A12. Only worth checking
      // while the row is small enough to be a legal layout at all.
      if (label && seatCount > 0 && seatCount <= MAX_SEATS) {
        for (let seatNumber = 1; seatNumber <= seatCount; seatNumber += 1) {
          const seatLabel = `${label}${seatNumber}`;
          if (generatedLabels.has(seatLabel)) {
            add(
              `Seat label "${seatLabel}" is generated more than once; change a row label or its seat count.`,
              target,
            );
            break;
          }
          generatedLabels.add(seatLabel);
        }
      }
    }
  }

  if (rowCount > MAX_ROWS) add(`An event may have at most ${MAX_ROWS} rows.`);
  if (totalSeats > MAX_SEATS) add(`An event may have at most ${MAX_SEATS} seats.`);

  return { valid: errors.length === 0, errors };
}
