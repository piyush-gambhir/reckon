import { source } from '@/lib/source';
import { llms } from 'fumadocs-core/source';
import { site } from '@/lib/site';
import { getOtherSuiteProjects } from '@/lib/suite';

export const revalidate = false;

export function GET() {
  const agentPreamble =
    'reckon is an independent, personal open-source, agent-ready RCA workspace for any coding agent or agent harness that can run shell commands. It uses structured JSON output from wired CLIs, YAML where supported, read-only credentials and safety guidance, and runtime flag passthrough for automation across observability, CI/CD, and infrastructure.';
  const relatedProjects = getOtherSuiteProjects(site.repo)
    .map(({ name, href }) => `- ${name}: ${href}`)
    .join('\n');

  return new Response(
    `${agentPreamble}\n\n${llms(source).index()}\n\n## Related independent CLI projects\n\n${relatedProjects}\n`,
    {
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
      },
    },
  );
}
