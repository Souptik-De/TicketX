import { ChevronDown } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Reusable, fully styleable listbox used by the catalog filters (category,
 * city). The parent owns the value so the same hook state drives both the
 * search and the results grid.
 */
export function FilterListbox({
  options,
  value,
  onChange,
  selectedLabel,
  emptyLabel = "All",
  listboxId,
  optionIdPrefix,
  ariaLabel,
  icon,
  titleClassName = "catalog-search-category-title",
  triggerClassName = "catalog-search-category-trigger",
  listboxClassName = "catalog-category-listbox",
  optionClassName = "catalog-category-option",
  optionSelectedClassName = "catalog-category-option-selected",
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const triggerRef = useRef(null);
  const listboxRef = useRef(null);

  const optionId = (index) => `${optionIdPrefix}-option-${index}`;

  const open = () => {
    setIsOpen(true);
    setHighlightedIndex(value ? options.indexOf(value) : -1);
  };
  const close = () => {
    setIsOpen(false);
    setHighlightedIndex(-1);
  };
  const select = (nextValue) => {
    onChange(nextValue);
    close();
  };

  const handleTriggerKeyDown = useCallback(
    (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        open();
        setHighlightedIndex(event.key === "ArrowUp" ? options.length - 1 : 0);
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        setIsOpen((prev) => !prev);
      }
    },
    [options.length],
  );

  const handleListboxKeyDown = useCallback(
    (event) => {
      const lastIndex = options.length - 1;
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        triggerRef.current?.focus();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlightedIndex((index) => (index + 1) % Math.max(options.length, 1));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlightedIndex((current) => (current - 1 + Math.max(options.length, 1)) % Math.max(options.length, 1));
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        setHighlightedIndex(0);
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        setHighlightedIndex(lastIndex < 0 ? -1 : lastIndex);
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select(highlightedIndex >= 0 ? options[highlightedIndex] : "");
        return;
      }
    },
    [highlightedIndex, options],
  );

  const handleDocumentClick = useCallback(
    (event) => {
      if (listboxRef.current && !listboxRef.current.contains(event.target) && !triggerRef.current?.contains(event.target)) {
        close();
      }
    },
    [],
  );

  useEffect(() => {
    document.addEventListener("mousedown", handleDocumentClick);
    return () => document.removeEventListener("mousedown", handleDocumentClick);
  }, [handleDocumentClick]);

  useEffect(() => {
    if (!isOpen || highlightedIndex < 0) return;
    const option = listboxRef.current?.querySelector(`#${optionId(highlightedIndex)}`);
    option?.scrollIntoView({ block: "nearest" });
  }, [isOpen, highlightedIndex, optionId]);

  return (
    <div>
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={listboxId}
        aria-activedescendant={isOpen && highlightedIndex >= 0 ? optionId(highlightedIndex) : undefined}
        aria-label={ariaLabel}
        onClick={() => setIsOpen((prev) => !prev)}
        onKeyDown={handleTriggerKeyDown}
      >
        {icon}
        <span className={titleClassName}>{selectedLabel}</span>
        <ChevronDown size={16} aria-hidden="true" className="catalog-search-category-chevron" />
      </button>
      {isOpen && (
        <div
          ref={listboxRef}
          id={listboxId}
          role="listbox"
          aria-label={ariaLabel}
          className={listboxClassName}
          onMouseDown={(event) => event.preventDefault()}
          onKeyDown={handleListboxKeyDown}
        >
          <div
            id={optionId(-1)}
            role="option"
            aria-selected={!value}
            data-highlighted={highlightedIndex === -1}
            className={
              !value
                ? `${optionClassName} ${optionSelectedClassName}`
                : optionClassName
            }
            onMouseMove={() => setHighlightedIndex(-1)}
            onClick={() => select("")}
          >
            {emptyLabel}
          </div>
          {options.map((item, index) => (
            <div
              key={item}
              id={optionId(index)}
              role="option"
              aria-selected={value === item}
              data-highlighted={highlightedIndex === index}
              className={
                value === item
                  ? `${optionClassName} ${optionSelectedClassName}`
                  : optionClassName
              }
              onMouseMove={() => setHighlightedIndex(index)}
              onClick={() => select(item)}
            >
              {item}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
