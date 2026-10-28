import { Search, Tag, MapPin } from "lucide-react";

import { FilterListbox } from "./FilterListbox";

/**
 * The search bar shared by the homepage and the signed-in catalog. It is fully
 * controlled: the parent owns the hook state so the results grid can react to
 * the same query. Category and city filters are generic listboxes whose options
 * come from the backend facets endpoint instead of a hardcoded list.
 */
export function CatalogSearch({
  query,
  setQuery,
  category,
  setCategory,
  city,
  setCity,
  categories,
  cities,
  isSearching,
}) {
  const categoryLabel = category || "All categories";
  const cityLabel = city || "All cities";

  return (
    <div className="catalog-search" role="search">
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
        <span className="sr-only">Filter by category</span>
        <FilterListbox
          options={categories}
          value={category}
          onChange={setCategory}
          selectedLabel={categoryLabel}
          emptyLabel="All categories"
          listboxId="catalog-category-listbox"
          optionIdPrefix="catalog-category"
          ariaLabel={`Filter by category, currently ${categoryLabel}`}
          icon={<Tag size={18} aria-hidden="true" className="catalog-search-icon" />}
        />
        <span className="sr-only">Filter by city</span>
        <FilterListbox
          options={cities}
          value={city}
          onChange={setCity}
          selectedLabel={cityLabel}
          emptyLabel="All cities"
          listboxId="catalog-city-listbox"
          optionIdPrefix="catalog-city"
          ariaLabel={`Filter by city, currently ${cityLabel}`}
          icon={<MapPin size={18} aria-hidden="true" className="catalog-search-icon" />}
        />
      </div>
    </div>
  );
}
