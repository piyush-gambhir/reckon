import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// https://astro.build/config
export default defineConfig({
  site: 'https://rca-assist.pages.dev',
  integrations: [
    starlight({
      title: 'rca-assist',
      description:
        'A coding-agent workflow for production root-cause analysis. Drives Grafana, Jenkins, CubeAPM and a read-only ops toolbelt (aws, gh, kcat, rpk, mongosh, psql, mysql) through a disciplined cascade — from alert to written RCA.',
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/piyush-gambhir/rca-assist',
        },
      ],
      editLink: {
        baseUrl: 'https://github.com/piyush-gambhir/rca-assist/edit/main/web/',
      },
      lastUpdated: true,
      sidebar: [
        {
          label: 'Get started',
          items: [{ autogenerate: { directory: 'guides' } }],
        },
        {
          label: 'Reference',
          items: [{ autogenerate: { directory: 'reference' } }],
        },
      ],
      customCss: ['./src/styles/custom.css'],
    }),
  ],
});
