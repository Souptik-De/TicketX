import { Search, Tag } from "lucide-react";

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
 * the same query.
 */
export function CatalogSearch({ query, setQuery, category, setCategory, isSearching }) {
  return (
    <div className="catalog-search" role="search">
      <label className="catalog-search-field">
        <Search size={18} aria-hidden="true" className="catalog-search-icon" />
        <span className="sr-only">Search events</span>
        <input
          type="search"
          value={query}
          onChange={(changeEvent) => setQuery(changeEvent.target.value)}
          placeholder="Search events by title, artist, venue, city..."
          aria-label="Search events"
        />
        {isSearching && <span className="catalog-search-status" aria-hidden="true">…</span>}
      </label>
      <label className="catalog-search-category">
        <Tag size={18} aria-hidden="true" className="catalog-search-icon" />
        <span className="sr-only">Filter by category</span>
        <select value={category} onChange={(changeEvent) => setCategory(changeEvent.target.value)} aria-label="Filter by category">
          <option value="">All categories</option>
          {CATEGORIES.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
