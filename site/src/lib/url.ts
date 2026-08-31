/**
 * Astro's `base` is a deployment fact (GitHub Pages project site today, custom
 * domain later) and every internal href has to carry it. One helper, used
 * everywhere, so switching to a custom domain is one env var and not a
 * find-and-replace across the site.
 */
const BASE = (import.meta.env.BASE_URL ?? '/').replace(/\/$/, '');

export const withBase = (path: string): string => `${BASE}${path.startsWith('/') ? path : `/${path}`}`;

export const recordPath = (name: string): string => withBase(`/record/${name}/`);
