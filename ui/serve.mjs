#!/usr/bin/env node
// reckon incidents viewer — zero-dependency local server.
//
//   node ui/serve.mjs            # http://localhost:7777
//   PORT=8080 node ui/serve.mjs
//
// Read-only: serves the incidents/ corpus (and legacy root RCA-*.md files)
// plus ui/index.html. Binds 127.0.0.1 only — incident folders contain real
// production identifiers and must not be exposed on the network.

import { createServer } from 'node:http';
import { readFile, readdir, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, resolve, dirname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const INCIDENTS = join(ROOT, 'incidents');
const PORT = Number(process.env.PORT || 7777);

// --- helpers ---------------------------------------------------------------

const json = (res, code, body) => {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(body));
};

const text = (res, code, body, type = 'text/plain') => {
  res.writeHead(code, { 'Content-Type': `${type}; charset=utf-8` });
  res.end(body);
};

// Only allow simple folder/file names — no separators, no traversal.
const safeName = (s) => typeof s === 'string' && s.length > 0 && !s.includes('/') && !s.includes('\\') && !s.includes('..');

// Confirm a resolved path stays inside an allowed base directory.
const inside = (base, p) => {
  const r = resolve(p);
  return r === base || r.startsWith(base + sep);
};

// Pull a field out of an RCA.md header table: matches `| **Key** | value |`
// and `| Key | value |` variants.
const headerField = (md, ...keys) => {
  for (const key of keys) {
    const re = new RegExp(`^\\|\\s*\\*{0,2}${key}[^|]*\\|\\s*(.+?)\\s*\\|?\\s*$`, 'im');
    const m = md.match(re);
    if (m) return m[1].replace(/\*\*/g, '').trim();
  }
  return null;
};

const firstHeading = (md) => {
  const m = md.match(/^#\s+(.+)$/m);
  return m ? m[1].trim() : null;
};

// --- incident listing --------------------------------------------------------

async function listIncidents() {
  const out = [];

  if (existsSync(INCIDENTS)) {
    for (const entry of await readdir(INCIDENTS, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const dir = join(INCIDENTS, entry.name);
      const rcaPath = join(dir, 'RCA.md');
      if (!existsSync(rcaPath)) continue;
      const md = await readFile(rcaPath, 'utf8');
      let evidence = [];
      const evDir = join(dir, 'evidence');
      if (existsSync(evDir)) {
        evidence = (await readdir(evDir)).filter((f) => !f.startsWith('.'));
      }
      out.push({
        id: entry.name,
        kind: 'folder',
        title: firstHeading(md) || entry.name,
        date: headerField(md, 'Date') || entry.name.slice(0, 10),
        window: headerField(md, 'Window'),
        trigger: headerField(md, 'Triggered by', 'Trigger'),
        impact: headerField(md, 'Peak impact'),
        superseded: /Superseded — not canonical/i.test(md),
        hasAlert: existsSync(join(dir, 'alert.txt')),
        hasLearnings: existsSync(join(dir, 'learnings.md')),
        evidenceCount: evidence.length,
      });
    }
  }

  // Legacy root-level RCA-*.md files (pre-incidents/ convention).
  for (const f of await readdir(ROOT)) {
    if (!/^RCA-.+\.md$/i.test(f)) continue;
    const md = await readFile(join(ROOT, f), 'utf8');
    out.push({
      id: `legacy:${f}`,
      kind: 'legacy',
      title: firstHeading(md) || f,
      date: (f.match(/\d{4}-\d{2}-\d{2}/) || [null])[0],
      window: headerField(md, 'Window'),
      trigger: headerField(md, 'Triggered by', 'Trigger'),
      impact: headerField(md, 'Peak impact'),
      superseded: false,
      hasAlert: false,
      hasLearnings: false,
      evidenceCount: 0,
    });
  }

  out.sort((a, b) => String(b.date).localeCompare(String(a.date)));
  return out;
}

async function getIncident(id) {
  if (id.startsWith('legacy:')) {
    const f = id.slice('legacy:'.length);
    if (!safeName(f) || !/^RCA-.+\.md$/i.test(f)) return null;
    const p = join(ROOT, f);
    if (!inside(ROOT, p) || !existsSync(p)) return null;
    return { id, rca: await readFile(p, 'utf8'), alert: null, learnings: null, evidence: [] };
  }
  if (!safeName(id)) return null;
  const dir = join(INCIDENTS, id);
  if (!inside(INCIDENTS, dir) || !existsSync(join(dir, 'RCA.md'))) return null;
  const read = async (name) => (existsSync(join(dir, name)) ? readFile(join(dir, name), 'utf8') : null);
  let evidence = [];
  const evDir = join(dir, 'evidence');
  if (existsSync(evDir)) evidence = (await readdir(evDir)).filter((f) => !f.startsWith('.'));
  return {
    id,
    rca: await read('RCA.md'),
    alert: await read('alert.txt'),
    learnings: await read('learnings.md'),
    evidence,
  };
}

async function getEvidence(id, file) {
  if (!safeName(id) || !safeName(file)) return null;
  const p = join(INCIDENTS, id, 'evidence', file);
  if (!inside(INCIDENTS, p) || !existsSync(p)) return null;
  const s = await stat(p);
  if (s.size > 5 * 1024 * 1024) return { tooLarge: true, size: s.size };
  return { content: await readFile(p, 'utf8') };
}

// --- server ------------------------------------------------------------------

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://localhost:${PORT}`);
    if (url.pathname === '/' || url.pathname === '/index.html') {
      return text(res, 200, await readFile(join(__dirname, 'index.html'), 'utf8'), 'text/html');
    }
    if (url.pathname === '/api/incidents') {
      return json(res, 200, await listIncidents());
    }
    if (url.pathname === '/api/incident') {
      const data = await getIncident(url.searchParams.get('id') || '');
      return data ? json(res, 200, data) : json(res, 404, { error: 'not found' });
    }
    if (url.pathname === '/api/evidence') {
      const data = await getEvidence(url.searchParams.get('id') || '', url.searchParams.get('file') || '');
      return data ? json(res, 200, data) : json(res, 404, { error: 'not found' });
    }
    json(res, 404, { error: 'not found' });
  } catch (err) {
    console.error('Incident viewer request failed:', err);
    json(res, 500, { error: 'internal server error' });
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`reckon incidents viewer → http://localhost:${PORT}`);
  console.log(`serving: ${INCIDENTS} (+ legacy root RCA-*.md)`);
});
