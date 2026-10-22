import { source } from '@/lib/source';
import { llms } from 'fumadocs-core/source';
import { site } from '@/lib/site';
import { siteUrl } from '@/lib/shared';
import { getOtherSuiteProjects } from '@/lib/suite';

export const revalidate = false;

export async function GET() {
  const agentPreamble =
    'reckon is an independent, personal open-source agent workspace for development across orgs and environments, for any coding agent or agent harness that can run shell commands. Each org has its own environments, tool connections, knowledge and saved work. Every tool call goes through reckon run, which adds that connection\'s credentials, keeps reads read-only and passes through the wired CLIs\' structured JSON output.';
  // index() returns a Promise since fumadocs-core 16.15.17. Its links are
  // root-relative, so make them absolute to keep the /reckon prefix.
  const index = (await llms(source).index()).replace(
    /\]\((\/[^)]+)\)/g,
    (_match, path: string) => `](${siteUrl}${path})`,
  );
  const relatedProjects = getOtherSuiteProjects(site.repo)
    .map(({ name, href }) => `- ${name}: ${href}`)
    .join('\n');

  return new Response(
    `${agentPreamble}\n\n${index}\n\n## Related independent CLI projects\n\n${relatedProjects}\n`,
    {
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
      },
    },
  );
}
