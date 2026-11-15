export const appName = 'reckon';
export const siteUrl = 'https://projects.piyushgambhir.com/reckon';
export const docsRoute = '/docs';
export const docsImageRoute = '/og/docs';
export const docsContentRoute = '/llms.mdx/docs';

export function sourcePath(pagePath: string): string {
  return `web/content/docs/${pagePath}`;
}

export const gitConfig = {
  user: 'piyush-gambhir',
  repo: 'reckon',
  branch: 'main',
};
