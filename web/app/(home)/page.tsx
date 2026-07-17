import Link from 'next/link';
import { CtaBand } from '@/components/cta-band';
import { HomeHero } from '@/components/home-hero';
import { IncidentPanel } from '@/components/incident-panel';
import { RcaSteps } from '@/components/rca-steps';
import { Reveal } from '@/components/reveal';
import { SiteFooter } from '@/components/site-footer';
import { ToolbeltSlider } from '@/components/toolbelt-slider';
import {
  licenseUrl,
  repositoryUrl,
  site,
  siteMetadataDescription,
} from '@/lib/site';
import { siteUrl } from '@/lib/shared';
import { getOtherSuiteProjects } from '@/lib/suite';

const contextualBodyLinks: Record<
  string,
  { href: string; text: string }
> = {
  'Incident investigations': {
    href: '/docs/quickstart',
    text: 'disciplined RCA workflow',
  },
  'Cross-system correlation': {
    href: '/docs/commands/observability',
    text: 'error and latency windows',
  },
  'Read-only safety': {
    href: '/docs/connections',
    text: 'read-only roles',
  },
  'One operations toolbelt': {
    href: '/docs/toolbelt',
    text: 'twelve wired CLIs',
  },
  'Runtime-agnostic agents': {
    href: '/docs/agents',
    text: 'Claude Code, Codex CLI, OpenCode',
  },
  'Credential isolation': {
    href: '/docs/connections',
    text: 'CLI profiles and environment credentials',
  },
};

export default function HomePage() {
  const relatedLinks = getOtherSuiteProjects(site.repo).map(({ href }) => href);
  const softwareApplication = {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    '@id': `${siteUrl}/#software-application`,
    name: site.name,
    applicationCategory: 'DeveloperApplication',
    operatingSystem: ['macOS', 'Linux', 'Windows with WSL2'],
    license: licenseUrl,
    url: siteUrl,
    sameAs: [repositoryUrl],
    description: siteMetadataDescription,
    relatedLink: relatedLinks,
    featureList: [
      'Structured JSON output for agents, plus YAML in wired CLIs that support it',
      'Read-only safety through roles, safeguards, allowlists, and approval prompts',
      'Non-interactive automation through shell commands and agent runtime flag passthrough',
      'Works with any coding agent or agent harness that can run shell commands',
    ],
    keywords: [
      'coding agent',
      'AI agent CLI',
      'agent harness',
      'MCP-free shell integration',
      'terminal automation',
      'reckon automation',
    ],
  };
  const website = {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    '@id': `${siteUrl}/#website`,
    name: site.name,
    url: siteUrl,
    inLanguage: 'en',
    sameAs: [repositoryUrl],
    description: siteMetadataDescription,
    relatedLink: relatedLinks,
  };

  return (
    <main className="osmo-home flex flex-1 flex-col">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: serializeJsonLd(softwareApplication) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: serializeJsonLd(website) }}
      />
      <HomeHero />

      {/* Stack strip */}
      {site.compatible && site.compatible.length > 0 ? (
        <section
          className="osmo-section osmo-section--compatible"
          aria-labelledby="compatible-heading"
        >
          <div className="osmo-container">
            <Reveal className="osmo-section__header">
              <h2 id="compatible-heading" className="compatible-heading">
                Speaks the language of your stack
              </h2>
            </Reveal>
            <Reveal className="compatible-marquee">
              <div className="compatible-marquee__track">
                {[false, true].map((hidden) => (
                  <span
                    className="compatible-marquee__list"
                    aria-hidden={hidden || undefined}
                    key={String(hidden)}
                  >
                    {site.compatible?.map((item) => (
                      <span className="compatible-marquee__item" key={item}>
                        {item}
                        <span aria-hidden>{' · '}</span>
                      </span>
                    ))}
                  </span>
                ))}
              </div>
            </Reveal>
          </div>
        </section>
      ) : null}

      <RcaSteps />

      {/* Features */}
      <section
        className="osmo-section osmo-section--features"
        aria-labelledby="features-heading"
      >
        <div className="osmo-container">
          <Reveal className="osmo-section__header">
            <h2 id="features-heading" className="osmo-section__title">
              {site.featuresTitle ?? 'Everything, from one binary'}
            </h2>
            <p className="osmo-section__subtitle">
              {site.featuresSubtitle ??
                'Built for humans at the keyboard and coding agents alike.'}
            </p>
          </Reveal>

          <Reveal className="osmo-card-grid osmo-card-grid--features" stagger>
            {site.features.map(({ icon: Icon, title, body }, index) => (
              <article
                key={title}
                className="osmo-card osmo-feature-card"
                data-reveal-item
              >
                <div className="osmo-feature-card__top">
                  <span className="osmo-feature-card__icon" aria-hidden="true">
                    <Icon />
                  </span>
                  <span className="osmo-eyebrow osmo-card__number">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                </div>
                <h3 className="osmo-card__title">{title}</h3>
                <p className="osmo-card__body">
                  <ContextualBody
                    body={body}
                    link={contextualBodyLinks[title]}
                  />
                </p>
              </article>
            ))}
          </Reveal>
        </div>
      </section>

      <ToolbeltSlider />

      <IncidentPanel />

      {/* CTA band */}
      <section className="osmo-section osmo-section--cta">
        <div className="osmo-container">
          <Reveal className="osmo-cta-card__reveal">
            <CtaBand />
          </Reveal>
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}

function ContextualBody({
  body,
  link,
}: {
  body: string;
  link?: { href: string; text: string };
}) {
  if (!link || !body.includes(link.text)) return body;

  const [before, after] = body.split(link.text, 2);

  return (
    <>
      {before}
      <Link href={link.href}>{link.text}</Link>
      {after}
    </>
  );
}

function serializeJsonLd(value: object): string {
  return JSON.stringify(value).replace(/</g, '\\u003c');
}
