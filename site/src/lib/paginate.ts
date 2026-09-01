/**
 * How many records one page of the catalogue holds.
 *
 * The catalogue (`/`) is one page: the whole record set is server-rendered so
 * zero-JS readers and crawlers see everything, and the island paginates the
 * *filtered* set client-side via `?page=N`. This constant is the page size it
 * uses; `index.astro` reads it and passes it to the island so the two agree.
 */
export const CATALOGUE_PAGE_SIZE = 20;
