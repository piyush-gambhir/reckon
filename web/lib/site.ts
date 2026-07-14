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
}

export const site: SiteConfig = {
  name: 'reckon',
  binary: 'reckon',
  repo: 'piyush-gambhir/reckon',
  tagline: 'Ask your infrastructure what went wrong.',
  description:
    'An agent workspace wiring read-only observability, CI/CD, and infrastructure CLIs into one isolated credential environment so a coding agent can investigate incidents, run RCAs, and understand production behavior.',
  badge: 'Production RCA workspace',
  installCommand:
    'git clone https://github.com/piyush-gambhir/reckon.git && cd reckon && bash scripts/setup.sh',
  features: [
    {
      icon: Search,
      title: 'Incident investigations',
      body: 'Move from an alert to traces, metrics, logs, deployment history, and infrastructure state with a disciplined RCA workflow.',
    },
    {
      icon: GitBranch,
      title: 'Cross-system correlation',
      body: 'Match error and latency windows with builds, releases, cluster events, queue lag, cache health, and database evidence.',
    },
    {
      icon: ListChecks,
      title: 'Read-only safety',
      body: 'Production-only conventions, read-only roles, session safeguards, command allowlists, and approval prompts keep investigations read-shaped.',
    },
    {
      icon: Zap,
      title: 'One operations toolbelt',
      body: 'Use twelve wired CLIs plus optional Elasticsearch access without switching credential contexts or stitching together separate workspaces.',
    },
    {
      icon: Bot,
      title: 'Runtime-agnostic agents',
      body: 'Run the same workspace under Claude Code, Codex CLI, OpenCode, or another agent runtime that follows AGENTS.md.',
    },
    {
      icon: KeyRound,
      title: 'Credential isolation',
      body: 'direnv pins CLI profiles and environment credentials inside the clone, keeping production access separate from global configuration.',
    },
  ],
  exampleTitle: 'An incident investigation',
  example: `# Assess alerts and failed delivery jobs
grafana alert rule list -o json
jenkins job list --recursive --status FAILURE -o json
# Follow the affected service into traces
cubeapm traces search --service checkout --status error --last 1h -o json
# Check whether the failure lines up with cluster events
kubectl get events -n checkout --sort-by=.lastTimestamp | tail -30
# Correlate the timestamps and save the RCA under incidents/`,
  featuresTitle: "Everything, in one workspace",
  featuresSubtitle: "A read-only toolbelt for a coding agent that investigates like your best on-call.",
  ctaBody: "Clone the workspace, run the idempotent setup script, add credentials, and start investigating.",
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
    'Elasticsearch',
  ],
};
