# web/

Docs and landing site for `rca-assist`. Astro + Starlight, deployed to Cloudflare Pages.

## Run locally

```bash
cd web
npm install
npm run dev
```

Open <http://localhost:4321>.

## Build

```bash
npm run build      # output to web/dist/
npm run preview    # serve the built site locally
```

## Deploy (Cloudflare Pages)

The site builds to static HTML/CSS/JS — Cloudflare Pages can deploy it directly.

**Option A — GitHub integration (recommended for first deploy).**

1. In the Cloudflare dashboard: Pages → Create a project → Connect to Git → pick this repo.
2. Build settings:
   - Framework preset: **Astro**
   - Build command: `npm run build`
   - Build output directory: `dist`
   - Root directory: `web`
3. Pages will redeploy on every push to `main`.

**Option B — Wrangler CLI (one-shot deploys without GitHub integration).**

```bash
npm install -g wrangler
cd web
npm run build
wrangler pages deploy dist --project-name rca-assist
```

`.wrangler/` is gitignored so any local cache stays out of the repo.

## Adding content

Pages live under `src/content/docs/` and use Markdown / MDX. The sidebar is auto-generated from the directory structure (`guides/` and `reference/` per `astro.config.mjs`). To add a new page, drop a new `.md` file into the matching directory; it appears in the sidebar on the next dev-server reload.

Frontmatter contract (Starlight):

```markdown
---
title: Page title
description: One sentence shown in search results and link previews.
---
```

## Structure

```
web/
├── astro.config.mjs            # Starlight config: title, sidebar, edit links
├── package.json                # astro, @astrojs/starlight, sharp
├── tsconfig.json
├── public/                     # static assets served at /
│   └── favicon.svg
└── src/
    ├── content.config.ts       # Starlight collection config (loader + schema)
    ├── content/docs/
    │   ├── index.mdx           # landing page (template: splash)
    │   ├── guides/             # "Get started" sidebar group
    │   │   ├── installation.md
    │   │   ├── first-investigation.md
    │   │   └── customizing-infra-knowledge.md
    │   └── reference/          # "Reference" sidebar group
    │       ├── skill-overview.md
    │       ├── incidents-folder.md
    │       └── post-incident-self-review.md
    └── styles/
        └── custom.css          # small overrides on top of Starlight defaults
```

## Why Astro + Starlight

- Markdown / MDX out of the box — no JSX boilerplate to write content.
- Built-in docs sidebar, search (Pagefind), dark mode, mobile responsive — none of which I'd want to maintain by hand.
- Static output by default (no JS shipped unless a page opts in) — fast on Pages, friendly to scrapers / archive.org.
- Single-repo: docs evolve in lockstep with `skills/rca-assist/SKILL.md` and `incidents/README.md`. The site can never drift from the skill it documents.
