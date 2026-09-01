/**
 * How many records a browse page holds.
 *
 * One constant, because two places need to agree: the route that paginates and
 * the sitemap that has to declare every page the route produced. They did not,
 * and `/browse/2/` was reachable by a real link but absent from sitemap.xml
 * (product-e2e-07, site-08).
 */
export const BROWSE_PAGE_SIZE = 20;
