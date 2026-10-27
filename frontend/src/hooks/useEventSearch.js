import { useEffect, useRef, useState } from "react";

import { searchEvents } from "../lib/api";

const DEBOUNCE_MS = 300;

/**
 * Debounced fuzzy event search.
 *
 * The catalog keeps showing the prop-supplied events until `hasQuery` is true:
 * an empty query with no category means "not searching", which both avoids a
 * pointless request and makes clearing the box restore the original list.
 * Each search effect owns an `isActive` flag, so a slow response that lands
 * after a newer one cannot overwrite newer results.
 */
export function useEventSearch() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [results, setResults] = useState([]);
  const [total, setTotal] = useState(0);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const hasQuery = query.trim().length > 0 || category.length > 0;
  const searchIdRef = useRef(0);

  // The debounce timer runs in this effect so retries on failure keep the same
  // delay the keystroke would have had; no state churn triggers it again.
  useEffect(() => {
    if (!hasQuery) {
      setResults([]);
      setTotal(0);
      setSearchError("");
      return undefined;
    }

    setIsSearching(true);
    const timer = setTimeout(() => {
      const searchId = searchIdRef.current + 1;
      searchIdRef.current = searchId;

      searchEvents({ q: query.trim(), category })
        .then((data) => {
          if (searchIdRef.current !== searchId) return;
          setResults(data.items ?? []);
          setTotal(data.total ?? 0);
          setSearchError("");
        })
        .catch((error) => {
          if (searchIdRef.current !== searchId) return;
          setResults([]);
          setTotal(0);
          setSearchError(error instanceof Error ? error.message : "Could not search events");
        })
        .finally(() => {
          if (searchIdRef.current === searchId) {
            setIsSearching(false);
          }
        });
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [query, category, hasQuery]);

  return { query, setQuery, category, setCategory, results, total, isSearching, searchError, hasQuery };
}
