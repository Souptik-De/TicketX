import { Search, Tag, ChevronDown } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

const CATEGORIES = [
  "concert",
  "festival",
  "workshop",
  "sports",
  "cultural",
  "comedy",
  "literary",
  "tech",
];

/**
 * The search bar shared by the homepage and the signed-in catalog. It is fully
 * controlled: the parent owns the hook state so the results grid can react to
 * the same query. The category filter uses a custom, fully styleable listbox
 * instead of a native <select> so dropdown text and selection states render
 * legibly in every browser/theme.
 */
export function CatalogSearch({ query, setQuery, category, setCategory, isSearching }) {
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const triggerRef = useRef(null);
  const listboxRef = useRef(null);
  const containerRef = useRef(null);

  const selectedLabel = category || "All categories";
  const optionId = (index) => `catalog-category-option-${index}`;

  const open = () => {
    setIsOpen(true);
    setHighlightedIndex(category ? CATEGORIES.indexOf(category) : -1);
  };
  const close = () => {
    setIsOpen(false);
    setHighlightedIndex(-1);
  };
  const selectCategory = (value) => {
    setCategory(value);
    close();
  };

  const handleTriggerKeyDown = useCallback(
    (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        open();
        setHighlightedIndex(event.key === "ArrowUp" ? CATEGORIES.length - 1 : 0);
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        setIsOpen((prev) => !prev);
      }
    },
    [],
  );

  const handleListboxKeyDown = useCallback(
    (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        triggerRef.current?.focus();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlightedIndex((index) => (index + 1) % CATEGORIES.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlightedIndex((current) => (current - 1 + CATEGORIES.length) % CATEGORIES.length);
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        setHighlightedIndex(0);
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        setHighlightedIndex(CATEGORIES.length - 1);
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectCategory(highlightedIndex >= 0 ? CATEGORIES[highlightedIndex] : "");
        return;
      }
    },
    [highlightedIndex],
  );

  const handleDocumentClick = useCallback(
    (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
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
    <div className="catalog-search" role="search" ref={containerRef}>
      <label className="catalog-search-field">
        <Search size={18} aria-hidden="true" className="catalog-search-icon" />
        <span className="sr-only">Search events</span>
        <input
          type="search"
          value={query}
          onChange={(changeEvent) => setQuery(changeEvent.target.value)}
          placeholder="Search events by title, artist, venue, city…"
          aria-label="Search events"
        />
        {isSearching && <span className="catalog-search-status" aria-hidden="true">…</span>}
      </label>
      <div className="catalog-search-category">
        <Tag size={18} aria-hidden="true" className="catalog-search-icon" />
        <span className="sr-only">Filter by category</span>
        <button
          ref={triggerRef}
          type="button"
          className="catalog-search-category-trigger"
          role="combobox"
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          aria-controls="catalog-category-listbox"
          aria-activedescendant={
            isOpen && highlightedIndex >= 0 ? optionId(highlightedIndex) : undefined
          }
          aria-label={`Filter by category, currently ${selectedLabel}`}
          onClick={() => setIsOpen((prev) => !prev)}
          onKeyDown={handleTriggerKeyDown}
        >
          <span className="catalog-search-category-title">{selectedLabel}</span>
          <ChevronDown size={16} aria-hidden="true" className="catalog-search-category-chevron" />
        </button>
        {isOpen && (
          <div
            ref={listboxRef}
            id="catalog-category-listbox"
            role="listbox"
            aria-label="Filter by category"
            className="catalog-category-listbox"
            onMouseDown={(event) => event.preventDefault()}
            onKeyDown={handleListboxKeyDown}
          >
            <div
              id={optionId(-1)}
              role="option"
              aria-selected={!category}
              data-highlighted={highlightedIndex === -1}
              className={
                !category
                  ? "catalog-category-option catalog-category-option-selected"
                  : "catalog-category-option"
              }
              onMouseMove={() => setHighlightedIndex(-1)}
              onClick={() => selectCategory("")}
            >
              All categories
            </div>
            {CATEGORIES.map((item, index) => (
              <div
                key={item}
                id={optionId(index)}
                role="option"
                aria-selected={category === item}
                data-highlighted={highlightedIndex === index}
                className={
                  category === item
                    ? "catalog-category-option catalog-category-option-selected"
                    : "catalog-category-option"
                }
                onMouseMove={() => setHighlightedIndex(index)}
                onClick={() => selectCategory(item)}
              >
                {item}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
