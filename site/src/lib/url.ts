/**
 * Astro's `base` is a deployment fact (GitHub Pages project site today, custom
 * domain later) and every internal href has to carry it. One helper, used
 * everywhere, so switching to a custom domain is one env var and not a
 * find-and-replace across the site.
 */
const BASE = (import.meta.env.BASE_URL ?? '/').replace(/\/$/, '');

export const withBase = (path: string): string => `${BASE}${path.startsWith('/') ? path : `/${path}`}`;

export const recordPath = (name: string): string => withBase(`/record/${name}/`);

/**
 * A catalogue filter link, in the one canonical shape every part of the site
 * uses: `${BASE_URL}?${param}=${value}` — e.g. `/iea-wind-data-catalogue/?task=task-43`.
 * `/` reads these query params and applies them; chips and badges only ever
 * build links of this shape, so there is a single filtering vocabulary
 * (task, kind, year, licence, source, institution, availability, q).
 */
export const filterHref = (param: string, value: string): string =>
  `${import.meta.env.BASE_URL}?${param}=${encodeURIComponent(value)}`;
