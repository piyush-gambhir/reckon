import {
  Bot,
  GitBranch,
  KeyRound,
  ListChecks,
  Search,
  Zap,
  type LucideIcon,
} from 'lucide-react';

export interface Feature {
  icon: LucideIcon;
  title: string;
  body: string;
}

export interface SiteConfig {
  /** Display name, e.g. "CubeAPM CLI" */
  name: string;
  /** The binary invoked in examples, e.g. "cubeapm" */
  binary: string;
  /** GitHub "owner/repo" */
  repo: string;
  /** One-line hero heading */
  tagline: string;
  /** Hero sub-paragraph */
  description: string;
  /** Small pill above the heading */
  badge: string;
  /** One-line install command shown in the hero */
  installCommand: string;
  /** Feature cards */
  features: Feature[];
  /** Title above the code block */
  exampleTitle: string;
  /** Shell example rendered in the terminal card */
  example: string;
  /** Optional: tech / query languages / integrations this CLI speaks (logo strip) */
  compatible?: string[];
  /** Optional: features section heading (default: "Everything, from one binary") */
  featuresTitle?: string;
  /** Optional: features section subheading */
  featuresSubtitle?: string;
  /** Optional: CTA band body (default mentions installing the binary) */
  ctaBody?: string;
  /** Optional: per-site accent expressed as an OKLCH color */
  accent?: string;
  /** Optional: human-readable accent name */
  accentName?: string;
  /** Optional: hex twin of the accent, for surfaces without oklch() support (OG images) */
  accentHex?: string;
}

export const site: SiteConfig = {
  name: 'reckon',
  binary: 'reckon',
  repo: 'piyush-gambhir/reckon',
  tagline: 'One agent workspace for every business you run.',
  description:
    'Reckon gives a coding agent one org at a time: its environments, tool connections, knowledge and saved work. Every tool call goes through Reckon, with the right account and read-only.',
  badge: 'Multi-business agent workspace',
  accent: 'oklch(0.85 0.21 150)',
  accentName: 'phosphor',
  accentHex: '#45f27f',
  installCommand:
    'git clone https://github.com/piyush-gambhir/reckon.git && cd reckon && bash scripts/setup.sh',
  features: [
    {
      icon: GitBranch,
      title: 'Orgs and environments',
      body: 'Each business keeps its own environments, connections, knowledge and sessions. An agent opens one org in one environment and cannot step outside it.',
    },
    {
      icon: Zap,
      title: 'Every tool through one command',
      body: 'Jenkins, CubeAPM, Grafana, GitHub, Jira, Search Console, Bing, Clarity and more through reckon run, each with the connection that matches the environment.',
    },
    {
      icon: ListChecks,
      title: 'Read-only by construction',
      body: 'Reckon forces each CLI\'s read-only mode and refuses arguments that change the target, the credentials or the environment. Database runs ask you first.',
    },
    {
      icon: KeyRound,
      title: 'Credential isolation',
      body: 'Each connection keeps its credentials and CLI profile under ~/.reckon, apart from your global configuration and out of the agent\'s environment.',
    },
    {
      icon: Bot,
      title: 'Saved work',
      body: 'Sessions keep every read with its command, connection and hashes. Findings cite evidence, and the work survives the conversation.',
    },
    {
      icon: Search,
      title: 'Incident investigations',
      body: 'Move from an alert to traces, metrics, logs, deployment history and infrastructure state with a disciplined RCA workflow.',
    },
  ],
  exampleTitle: 'One business, one environment',
  example: `# Start the agent on one org, pinned to production
reckon open acme/prod
# Inside it, every tool goes through Reckon
reckon run jenkins -- build list deploy-api --limit 10 -o json
reckon run cubeapm -- traces search --service checkout --status error --last 1h -o json
reckon run gsc -- top queries --site sc-domain:acme.com --last 28d -o json
# Keep the evidence with the work
reckon task --question "Why did signups drop last week?" --area gtm`,
  featuresTitle: "Everything, in one workspace",
  featuresSubtitle: "Development, operations, product and growth tools for a coding agent, one business at a time.",
  ctaBody: "Clone the workspace, run the setup script, add an org and its connections, and open it.",
  compatible: [
    'Grafana',
    'Jenkins',
    'CubeAPM',
    'AWS',
    'GitHub Actions',
    'Kafka',
    'Kubernetes',
    'Redis',
    'MongoDB',
    'PostgreSQL',
    'MySQL',
    'ClickHouse',
    'Elasticsearch',
    'Jira',
    'Nginx Proxy Manager',
    'Search Console',
    'Bing Webmaster Tools',
    'Microsoft Clarity',
  ],
};

export const repositoryUrl = `https://github.com/${site.repo}`;
export const licenseUrl = `${repositoryUrl}/blob/main/LICENSE`;
export const projectDescription =
  'reckon is an independent, personal open-source agent workspace for running several businesses: orgs, environments, tool connections and saved work, read-only.';
export const siteMetadataDescription =
  'Independent personal open-source agent workspace: one org at a time, every tool through reckon run, read-only, with saved sessions and incident investigations.';
