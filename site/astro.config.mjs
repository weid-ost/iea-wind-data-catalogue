import { defineConfig } from 'astro/config';

// No integrations, no UI framework, no image pipeline (ADR-0032: "keep the
// Astro layer thin — every integration is a future migration"). Pagefind runs
// after `astro build` as a separate binary, not as an integration.
//
// SITE_URL / SITE_BASE exist because the deployment target is a GitHub Pages
// *project* site today and a custom domain later. Defaults match the repo as it
// stands; a custom domain sets SITE_BASE=/ and SITE_URL=https://your.domain/.
export default defineConfig({
  site: process.env.SITE_URL ?? 'https://thclark.github.io/iea-wind-data-catalogue/',
  base: process.env.SITE_BASE ?? '/iea-wind-data-catalogue',
  trailingSlash: 'always',
  build: { format: 'directory' },
  devToolbar: { enabled: false },
  compressHTML: true,
  markdown: { syntaxHighlight: false },
});
