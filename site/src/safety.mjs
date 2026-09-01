// Render safety — the last line, applied where a harvested string becomes markup.
//
// Everything in `records/*.json` is somebody else's text: a registrant's title,
// an author's description, a curator's pasted link. `harvest/sanitize.py` and
// `harvest/urls.py` already clean those on the way *in*, and they are the right
// place for it. This file is the belt to that pair of braces, because the
// renderer must not depend on every present and future adapter having
// remembered (site-01, site-02, scrape-01):
//
//   * `jsonForHtml`  — JSON.stringify does NOT escape `<`, so a title
//     containing `</script>` closes the JSON-LD block and everything after it
//     is live HTML. Verified end-to-end before this existed.
//   * `safeHref`     — escaping an attribute does not disarm `javascript:`.
//     Same allow-list as `harvest.urls.ALLOWED_URL_SCHEMES`, same
//     browser-shaped cleaning (C0 controls and tab/CR/LF removed before the
//     scheme is read).
//   * `safeHtml`     — the tag/attribute allow-list of
//     `harvest.sanitize`, re-implemented for the render boundary. Nothing is
//     passed through: every tag it keeps is *reconstructed* from parsed parts,
//     so a token it mis-reads becomes text rather than markup.
//
// Plain `.mjs`, like `ckan.mjs` and `licenses.mjs`, so `scripts/check-render.mjs`
// can import and exercise it from bare node — a gate nobody has watched fail is
// a gate you are guessing about. `tests/test_site_render_safety.py` asserts the
// allow-lists here still match the Python ones.

// ---------------------------------------------------------------- URLs

/** Mirrors `harvest.urls.ALLOWED_URL_SCHEMES`. */
export const ALLOWED_URL_SCHEMES = Object.freeze([
  'http',
  'https',
  'mailto',
  'ftp',
  'ftps',
]);

/** Mirrors `harvest.sanitize.ALLOWED_SCHEMES` — the stricter policy for hrefs
 *  inside a harvested description, where `ftp` has no business appearing. */
export const ALLOWED_HTML_SCHEMES = Object.freeze(['http', 'https', 'mailto']);

const C0_AND_SPACE = '\\u0000-\\u0020\\u007f';
const TRIM_RE = new RegExp(`^[${C0_AND_SPACE}]+|[${C0_AND_SPACE}]+$`, 'g');
const BLANKS_RE = new RegExp(`[${C0_AND_SPACE}]+`, 'g');
const TAB_NEWLINE_RE = /[\t\r\n]/g;
const SCHEME_RE = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;

/** The URL as a browser would see it: NUL/tab/CR/LF removed, C0 trimmed. */
export function cleanUrl(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/\u0000/g, '').replace(TAB_NEWLINE_RE, '').replace(TRIM_RE, '');
}

/**
 * The cleaned URL if its scheme is one we are willing to link to, else
 * `undefined`. What the check approves is what the page links to — the classic
 * shape of this bug is probing a scrubbed copy and emitting the raw string.
 */
export function safeHref(value, { allowRelative = false, schemes = ALLOWED_URL_SCHEMES } = {}) {
  const allowed = new Set(schemes.map((s) => s.toLowerCase()));
  const cleaned = cleanUrl(value);
  if (!cleaned) return undefined;
  // The probe is what a browser resolves: every C0 control and space removed
  // from anywhere in the string, so `javascript:` cannot hide behind one.
  const probe = cleaned.replace(BLANKS_RE, '');
  const match = SCHEME_RE.exec(probe);
  if (match) return allowed.has(match[1].toLowerCase()) ? cleaned : undefined;
  // A protocol-relative `//host/path` inherits the page's scheme, so it cannot
  // be checked here and is always refused.
  if (probe.startsWith('//')) return undefined;
  return allowRelative ? cleaned : undefined;
}

export const isSafeHref = (value, options) => safeHref(value, options) !== undefined;

/** Keep only the entries whose `url` is linkable. A resource IS a link. */
export const safeLinks = (entries) =>
  (Array.isArray(entries) ? entries : [])
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return undefined;
      const url = safeHref(entry.url);
      return url === undefined ? undefined : { ...entry, url };
    })
    .filter((entry) => entry !== undefined);

// ---------------------------------------------------------------- JSON in HTML

const JSON_ESCAPES = {
  '<': '\\u003c',
  '>': '\\u003e',
  '&': '\\u0026',
  '\u2028': '\\u2028',
  '\u2029': '\\u2029',
};

/**
 * JSON, safe to embed inside an HTML `<script>` element.
 *
 * `JSON.stringify` escapes quotes and backslashes but not `<`, `>` or `&`, so
 * a harvested title containing `</script>` ends the element and the rest of the
 * title is parsed as HTML (scrape-01: verified to execute `<img src=x
 * onerror=…>` from a Zenodo title). The three characters are escaped as JSON
 * unicode escapes, which every JSON parser reads back as the original string —
 * the data is unchanged, only its transport is made safe. U+2028/U+2029 are
 * escaped too: they are valid JSON but illegal raw in a JavaScript string.
 */
export function jsonForHtml(value, space) {
  return JSON.stringify(value, null, space).replace(
    /[<>&\u2028\u2029]/g,
    (ch) => JSON_ESCAPES[ch]
  );
}

// ---------------------------------------------------------------- HTML

/** Mirrors `harvest.sanitize.ALLOWED_TAGS`. */
export const ALLOWED_TAGS = Object.freeze([
  'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'dd', 'dl', 'dt', 'em',
  'h3', 'h4', 'h5', 'h6', 'i', 'li', 'ol', 'p', 'pre', 's', 'small',
  'span', 'strong', 'sub', 'sup', 'table', 'tbody', 'td', 'th', 'thead',
  'tr', 'u', 'ul',
]);

/** Mirrors `harvest.sanitize.ALLOWED_ATTRIBUTES`. Everything else — every
 *  `on*` handler, `style`, `class`, `id` — is dropped. */
export const ALLOWED_ATTRIBUTES = Object.freeze({
  a: ['href', 'title'],
  abbr: ['title'],
  td: ['colspan', 'rowspan'],
  th: ['colspan', 'rowspan', 'scope'],
});

/** Mirrors `harvest.sanitize.VOID_TAGS`. */
export const VOID_TAGS = Object.freeze(['br']);

/** Tags whose *content* goes with them. Mirrors `harvest.sanitize._DROP_CONTENT`. */
export const DROP_CONTENT_TAGS = Object.freeze([
  'script', 'style', 'iframe', 'object', 'embed', 'template', 'svg',
]);

const TAGS = new Set(ALLOWED_TAGS);
const VOIDS = new Set(VOID_TAGS);
const DROPPED = new Set(DROP_CONTENT_TAGS);

const NAMED_ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
};

/** Decode the entities a browser decodes *before* it reads an attribute, so
 *  `href="java&#115;cript:…"` is tested as `javascript:` and refused. */
export function decodeEntities(text) {
  return String(text ?? '').replace(/&(#x[0-9a-fA-F]+|#\d+|[a-zA-Z][a-zA-Z0-9]{1,31});?/g, (whole, body) => {
    if (body[0] === '#') {
      const code = body[1] === 'x' || body[1] === 'X'
        ? Number.parseInt(body.slice(2), 16)
        : Number.parseInt(body.slice(1), 10);
      if (!Number.isFinite(code) || code < 0 || code > 0x10ffff) return whole;
      try {
        return String.fromCodePoint(code);
      } catch {
        return whole;
      }
    }
    const named = NAMED_ENTITIES[body.toLowerCase()];
    return named === undefined ? whole : named;
  });
}

/** Escape text for HTML. Applied to every character this module emits. */
export const escapeHtml = (text) =>
  String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

const TAG_RE = /^<(\/?)([a-zA-Z][a-zA-Z0-9-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/;
const ATTR_RE = /([a-zA-Z_:][-\w:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

function renderAttributes(tag, raw) {
  const allowed = new Set(ALLOWED_ATTRIBUTES[tag] ?? []);
  if (allowed.size === 0) return '';
  const parts = [];
  const seen = new Set();
  ATTR_RE.lastIndex = 0;
  let match;
  while ((match = ATTR_RE.exec(raw)) !== null) {
    const name = match[1].toLowerCase();
    if (!allowed.has(name) || seen.has(name)) continue;
    const value = decodeEntities(match[2] ?? match[3] ?? match[4] ?? '');
    if (name === 'href') {
      const href = safeHref(value, { allowRelative: true, schemes: ALLOWED_HTML_SCHEMES });
      if (href === undefined) continue;
      seen.add(name);
      parts.push(` href="${escapeHtml(href)}"`);
      continue;
    }
    seen.add(name);
    parts.push(` ${name}="${escapeHtml(value)}"`);
  }
  return parts.join('');
}

/**
 * A harvested description, reduced to the allowed subset — the render-side
 * mirror of `harvest.sanitize.sanitize_html`.
 *
 * The site renders `notes` with `set:html` because a Zenodo abstract is real
 * (already-sanitised) markup and stripping it would lose paragraphs, lists and
 * links. That makes the renderer a sink, and before this the only defence was
 * every adapter remembering to sanitise (site-02). Now the boundary itself
 * refuses: script and its content are dropped, unknown tags become text,
 * every attribute outside the allow-list disappears, and every href is
 * scheme-checked.
 */
export function safeHtml(html) {
  const input = typeof html === 'string' ? html : '';
  const out = [];
  const open = [];
  let suppress = 0;
  let index = 0;

  const emitText = (text) => {
    if (!text || suppress > 0) return;
    out.push(escapeHtml(decodeEntities(text)));
  };

  while (index < input.length) {
    const lt = input.indexOf('<', index);
    if (lt === -1) {
      emitText(input.slice(index));
      break;
    }
    emitText(input.slice(index, lt));
    const rest = input.slice(lt);

    // Comments, doctypes and processing instructions: dropped whole.
    if (rest.startsWith('<!--')) {
      const end = input.indexOf('-->', lt + 4);
      index = end === -1 ? input.length : end + 3;
      continue;
    }
    if (rest.startsWith('<!') || rest.startsWith('<?')) {
      const end = input.indexOf('>', lt + 2);
      index = end === -1 ? input.length : end + 1;
      continue;
    }

    const match = TAG_RE.exec(rest);
    if (!match) {
      // Not a tag a browser would parse either — it is a literal `<`.
      emitText('<');
      index = lt + 1;
      continue;
    }

    const [whole, closing, rawName, rawAttrs] = match;
    const tag = rawName.toLowerCase();
    index = lt + whole.length;

    if (DROPPED.has(tag)) {
      if (closing) suppress = Math.max(0, suppress - 1);
      else if (!whole.endsWith('/>')) suppress += 1;
      continue;
    }
    if (suppress > 0 || !TAGS.has(tag)) continue;

    if (closing) {
      if (VOIDS.has(tag) || !open.includes(tag)) continue;
      while (open.length > 0) {
        const openTag = open.pop();
        out.push(`</${openTag}>`);
        if (openTag === tag) break;
      }
      continue;
    }

    const attributes = renderAttributes(tag, rawAttrs);
    if (VOIDS.has(tag) || whole.endsWith('/>')) {
      out.push(`<${tag}${attributes} />`);
      continue;
    }
    out.push(`<${tag}${attributes}>`);
    open.push(tag);
  }

  while (open.length > 0) out.push(`</${open.pop()}>`);
  return out.join('');
}
