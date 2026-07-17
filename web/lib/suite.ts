export interface SuiteProject {
  name: string;
  href: string;
}

export const suite: readonly SuiteProject[] = [
  { name: 'jira-cli', href: 'https://projects.piyushgambhir.com/jira-cli' },
  { name: 'cubeapm-cli', href: 'https://projects.piyushgambhir.com/cubeapm-cli' },
  { name: 'es-cli', href: 'https://projects.piyushgambhir.com/es-cli' },
  { name: 'grafana-cli', href: 'https://projects.piyushgambhir.com/grafana-cli' },
  { name: 'jenkins-cli', href: 'https://projects.piyushgambhir.com/jenkins-cli' },
  { name: 'nginxpm-cli', href: 'https://projects.piyushgambhir.com/nginxpm-cli' },
  { name: 'reckon', href: 'https://projects.piyushgambhir.com/reckon' },
];

export function getOtherSuiteProjects(currentSite: string): SuiteProject[] {
  const currentName = currentSite.split('/').at(-1)?.replace(/\.git$/, '');
  return suite.filter(({ name }) => name !== currentName);
}
