// A twenty-line static server for `dist/`, honouring the deployment base path.
//
// Deliberately not a dependency and deliberately not `astro preview`: Astro 7's
// preview is a daemon that survives the process which started it, which is the
// wrong lifecycle for a gate that must start a server, audit it and stop it.
// GitHub Pages serves plain files; so does this.
import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize } from 'node:path';

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.jsonld': 'application/ld+json; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8',
  '.woff2': 'font/woff2',
  '.svg': 'image/svg+xml',
  '.wasm': 'application/wasm',
  '.pagefind': 'application/octet-stream',
};

export function serve({ root, base = '/', port = 4321 }) {
  const prefix = base.replace(/\/$/, '');
  const server = createServer((request, response) => {
    const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const relative = prefix && path.startsWith(prefix) ? path.slice(prefix.length) : path;
    let file = join(root, normalize(relative).replace(/^(\.\.[/\\])+/, ''));
    if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
    if (!existsSync(file)) {
      response.writeHead(404, { 'content-type': 'text/plain' });
      response.end(`not found: ${path}`);
      return;
    }
    response.writeHead(200, {
      'content-type': TYPES[extname(file)] ?? 'application/octet-stream',
      'cache-control': 'no-store',
    });
    createReadStream(file).pipe(response);
  });
  return new Promise((resolve) => server.listen(port, () => resolve(server)));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const port = Number(process.env.PORT ?? 4321);
  const base = process.env.SITE_BASE ?? '/iea-wind-data-catalogue';
  await serve({ root: join(process.cwd(), 'dist'), base, port });
  console.log(`serving dist/ at http://localhost:${port}${base.replace(/\/$/, '')}/`);
}
