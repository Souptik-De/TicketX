import { useDeferredValue, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";

import {
  MAX_ROWS,
  MAX_TIERS,
  layoutRows,
  layoutToSeats,
  makeRow,
  makeTier,
  nextRowLabel,
  quickAddRows,
} from "../lib/layoutToSeats";
import { SeatMap } from "./SeatMap";

/**
 * Admin-side seat-layout editor.
 *
 * Controlled on purpose: the layout lives in one object in `AdminPanel`, so a
 * failed submit (a 422, say) can never clear what the admin typed. This
 * component only edits that object and previews it.
 *
 * The preview is deferred: a 2000-seat map re-rendering on every keystroke is
 * the one thing that would make this form feel broken, and `useDeferredValue`
 * gives React permission to skip the intermediate states for free.
 */
export function SeatLayoutBuilder({ mode, onModeChange, layout, onLayoutChange, validation, disabled }) {
  const tiers = layout?.tiers ?? [];
  const rowsNow = layoutRows(layout).length;

  const [quick, setQuick] = useState({
    tierId: null,
    start: null,
    count: "3",
    blocks: "10",
    error: "",
  });

  const activeTierId = tiers.some((tier) => tier.id === quick.tierId)
    ? quick.tierId
    : tiers[0]?.id ?? null;
  // A null start means "follow the layout". Editing the field pins it; a
  // successful add clears it again so the next batch continues from there.
  const startValue = quick.start ?? nextRowLabel(layout);

  const errors = validation?.errors ?? [];
  const globalErrors = errors.filter((error) => !error.tierId);
  const errorsByTier = new Map();
  const errorsByRow = new Map();
  for (const error of errors) {
    if (error.rowId) {
      const key = `${error.tierId}:${error.rowId}`;
      errorsByRow.set(key, [...(errorsByRow.get(key) ?? []), error.message]);
    } else if (error.tierId) {
      errorsByTier.set(error.tierId, [...(errorsByTier.get(error.tierId) ?? []), error.message]);
    }
  }

  const deferredLayout = useDeferredValue(layout);
  const preview = useMemo(() => layoutToSeats(deferredLayout), [deferredLayout]);

  function mapTier(tierId, updater) {
    onLayoutChange({
      ...layout,
      tiers: tiers.map((tier) => (tier.id === tierId ? updater(tier) : tier)),
    });
  }

  function moveTier(index, direction) {
    const target = index + direction;
    if (target < 0 || target >= tiers.length) return;
    const next = [...tiers];
    [next[index], next[target]] = [next[target], next[index]];
    onLayoutChange({ ...layout, tiers: next });
  }

  function addTier() {
    if (tiers.length >= MAX_TIERS) return;
    onLayoutChange({
      ...layout,
      tiers: [...tiers, makeTier({ name: `Tier ${tiers.length + 1}` })],
    });
  }

  function runQuickAdd() {
    const result = quickAddRows(layout, activeTierId, {
      start: startValue,
      count: quick.count,
      blocks: quick.blocks,
    });
    if (result.error) {
      setQuick((current) => ({ ...current, error: result.error }));
      return;
    }
    onLayoutChange(result.layout);
    setQuick((current) => ({ ...current, start: null, error: "" }));
  }

  return (
    <div
      className="seat-builder"
      onKeyDown={(keyEvent) => {
        // The builder sits inside the create-event form, so Enter in one of its
        // fields would otherwise create the event while the admin is still
        // typing a layout.
        if (keyEvent.key === "Enter" && keyEvent.target.tagName === "INPUT") {
          keyEvent.preventDefault();
        }
      }}
    >
      <div className="builder-mode" role="group" aria-label="How this event sells seats">
        <button
          type="button"
          className="builder-mode-button"
          aria-pressed={mode === "simple"}
          onClick={() => onModeChange("simple")}
        >
          Simple (capacity only)
        </button>
        <button
          type="button"
          className="builder-mode-button"
          aria-pressed={mode === "map"}
          onClick={() => onModeChange("map")}
        >
          Seat map
        </button>
      </div>

      {mode === "map" && (
        <>
          <p className="muted-text builder-hint">
            Seats are numbered 1..n continuously across each row&apos;s blocks. Blocks are separated by a visible
            aisle on the map.
          </p>

          {globalErrors.length > 0 && (
            <ul className="builder-errors" role="alert">
              {globalErrors.map((error) => (
                <li key={error.message}>{error.message}</li>
              ))}
            </ul>
          )}

          {tiers.map((tier, tierIndex) => {
            const tierMessages = errorsByTier.get(tier.id) ?? [];
            return (
              <section className="builder-tier" key={tier.id} aria-label={`Tier ${tierIndex + 1}`}>
                <div className="builder-tier-head">
                  <label>
                    Tier name
                    <input
                      value={tier.name}
                      maxLength={80}
                      disabled={disabled}
                      onChange={(changeEvent) => mapTier(tier.id, (current) => ({ ...current, name: changeEvent.target.value }))}
                    />
                  </label>
                  <div className="builder-tier-actions">
                    <button
                      type="button"
                      className="builder-mini-button"
                      aria-label={`Move ${tier.name || "tier"} up`}
                      title="Move up"
                      disabled={disabled || tierIndex === 0}
                      onClick={() => moveTier(tierIndex, -1)}
                    >
                      <ArrowUp size={16} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="builder-mini-button"
                      aria-label={`Move ${tier.name || "tier"} down`}
                      title="Move down"
                      disabled={disabled || tierIndex === tiers.length - 1}
                      onClick={() => moveTier(tierIndex, 1)}
                    >
                      <ArrowDown size={16} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="builder-mini-button builder-remove"
                      aria-label={`Remove ${tier.name || "tier"}`}
                      title="Remove tier"
                      disabled={disabled}
                      onClick={() =>
                        onLayoutChange({ ...layout, tiers: tiers.filter((item) => item.id !== tier.id) })
                      }
                    >
                      <Trash2 size={16} aria-hidden="true" />
                    </button>
                  </div>
                </div>

                {tierMessages.map((message) => (
                  <p className="builder-error" key={message}>
                    {message}
                  </p>
                ))}

                <div className="builder-rows">
                  {(tier.rows ?? []).map((row, rowIndex) => {
                    const rowMessages = errorsByRow.get(`${tier.id}:${row.id}`) ?? [];
                    return (
                      <div className="builder-row-wrap" key={row.id}>
                        <div className="builder-row">
                          <label>
                            Row
                            <input
                              value={row.label}
                              maxLength={24}
                              disabled={disabled}
                              aria-label={`Tier ${tierIndex + 1} row ${rowIndex + 1} label`}
                              onChange={(changeEvent) =>
                                mapTier(tier.id, (current) => ({
                                  ...current,
                                  rows: current.rows.map((item) =>
                                    item.id === row.id ? { ...item, label: changeEvent.target.value } : item,
                                  ),
                                }))
                              }
                            />
                          </label>
                          <label>
                            Blocks
                            <input
                              value={row.blocks}
                              inputMode="numeric"
                              placeholder="3,14,3"
                              disabled={disabled}
                              aria-label={`Tier ${tierIndex + 1} row ${rowIndex + 1} blocks`}
                              onChange={(changeEvent) =>
                                mapTier(tier.id, (current) => ({
                                  ...current,
                                  rows: current.rows.map((item) =>
                                    item.id === row.id ? { ...item, blocks: changeEvent.target.value } : item,
                                  ),
                                }))
                              }
                            />
                          </label>
                          <label>
                            Blocked <span className="builder-optional">(optional)</span>
                            <input
                              value={row.blocked}
                              inputMode="numeric"
                              placeholder="4,5"
                              disabled={disabled}
                              aria-label={`Tier ${tierIndex + 1} row ${rowIndex + 1} blocked seats`}
                              onChange={(changeEvent) =>
                                mapTier(tier.id, (current) => ({
                                  ...current,
                                  rows: current.rows.map((item) =>
                                    item.id === row.id ? { ...item, blocked: changeEvent.target.value } : item,
                                  ),
                                }))
                              }
                            />
                          </label>
                          <button
                            type="button"
                            className="builder-mini-button builder-remove"
                            aria-label={`Remove row ${row.label || rowIndex + 1}`}
                            title="Remove row"
                            disabled={disabled}
                            onClick={() =>
                              mapTier(tier.id, (current) => ({
                                ...current,
                                rows: current.rows.filter((item) => item.id !== row.id),
                              }))
                            }
                          >
                            <Trash2 size={16} aria-hidden="true" />
                          </button>
                        </div>
                        {rowMessages.map((message) => (
                          <p className="builder-error" key={message}>
                            {message}
                          </p>
                        ))}
                      </div>
                    );
                  })}
                </div>

                <button
                  type="button"
                  className="ghost-button builder-add-row"
                  disabled={disabled || rowsNow >= MAX_ROWS}
                  onClick={() => mapTier(tier.id, (current) => ({ ...current, rows: [...(current.rows ?? []), makeRow()] }))}
                >
                  <Plus size={16} aria-hidden="true" />
                  Add row
                </button>
              </section>
            );
          })}

          <button
            type="button"
            className="ghost-button"
            disabled={disabled || tiers.length >= MAX_TIERS}
            onClick={addTier}
          >
            <Plus size={16} aria-hidden="true" />
            Add tier
          </button>

          <div
            className="builder-quick"
            onKeyDown={(keyEvent) => {
              // Enter in a text field adds the batch, matching the button. The
              // select keeps its native keyboard behaviour.
              if (keyEvent.key === "Enter" && keyEvent.target.tagName === "INPUT") {
                keyEvent.preventDefault();
                keyEvent.stopPropagation();
                runQuickAdd();
              }
            }}
          >
            <div className="builder-quick-heading">
              <strong>Quick add rows</strong>
              <small>Labels continue across the whole event.</small>
            </div>
            <label>
              Add to tier
              <select
                value={activeTierId ?? ""}
                disabled={disabled || tiers.length === 0}
                onChange={(changeEvent) => setQuick((current) => ({ ...current, tierId: changeEvent.target.value }))}
              >
                {tiers.map((tier) => (
                  <option key={tier.id} value={tier.id}>
                    {tier.name || "Untitled tier"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              First label
              <input
                value={startValue}
                disabled={disabled}
                aria-label="Quick add starting label"
                onChange={(changeEvent) => setQuick((current) => ({ ...current, start: changeEvent.target.value }))}
              />
            </label>
            <label>
              Rows
              <input
                type="number"
                min={1}
                max={MAX_ROWS}
                value={quick.count}
                disabled={disabled}
                onChange={(changeEvent) => setQuick((current) => ({ ...current, count: changeEvent.target.value }))}
              />
            </label>
            <label>
              Blocks pattern
              <input
                value={quick.blocks}
                inputMode="numeric"
                placeholder="3,14,3"
                disabled={disabled}
                onChange={(changeEvent) => setQuick((current) => ({ ...current, blocks: changeEvent.target.value }))}
              />
            </label>
            {quick.error && (
              <p className="builder-error full-width" role="alert">
                {quick.error}
              </p>
            )}
            <button className="ghost-button" type="button" disabled={disabled || !activeTierId} onClick={runQuickAdd}>
              <Plus size={16} aria-hidden="true" />
              Add rows
            </button>
          </div>

          <div className="builder-preview">
            <div className="row-heading">
              <h3>Live preview</h3>
              <span className="sync-state">{rowsNow} row{rowsNow === 1 ? "" : "s"}</span>
            </div>
            <p className="builder-totals">
              <span>
                Total seats <strong>{preview.totals.total}</strong>
              </span>
              <span>
                Blocked <strong>{preview.totals.blocked}</strong>
              </span>
              <span>
                Sellable <strong>{preview.totals.sellable}</strong>
              </span>
            </p>
            {preview.hidden ? (
              <p className="error-text">
                Preview hidden: the layout is over {preview.totals.total} seats. Reduce the block sizes to see it.
              </p>
            ) : (
              <SeatMap tiers={preview.tiers} readOnly />
            )}
          </div>
        </>
      )}
    </div>
  );
}
