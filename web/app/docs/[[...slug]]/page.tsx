import { getPageImage, getPageMarkdownUrl, source } from '@/lib/source';
import {
  DocsBody,
  DocsDescription,
  DocsPage,
  DocsTitle,
  MarkdownCopyButton,
  ViewOptionsPopover,
} from 'fumadocs-ui/layouts/docs/page';
import { notFound } from 'next/navigation';
import { getMDXComponents } from '@/components/mdx';
import type { Metadata } from 'next';
import { createRelativeLink } from 'fumadocs-ui/mdx';
import { absoluteUrl, createPageMetadata, describePage } from '@/lib/metadata';
import { gitConfig } from '@/lib/shared';
import {
  licenseUrl,
  projectDescription,
  repositoryUrl,
  site,
} from '@/lib/site';
import { siteUrl } from '@/lib/shared';
import type { ComponentProps } from 'react';

const lenisPrevent = {
  'data-lenis-prevent': '',
} as ComponentProps<'div'>;

export default async function Page(props: PageProps<'/docs/[[...slug]]'>) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  const MDX = page.data.body;
  const markdownUrl = `${siteUrl}${getPageMarkdownUrl(page).url}`;
  const summary =
    page.data.description ?? `Technical documentation for ${page.data.title}.`;
  const description = `${summary} ${projectDescription}`;
  const breadcrumbs = [
    {
      '@type': 'ListItem',
      position: 1,
      name: 'Home',
      item: absoluteUrl('/'),
    },
    {
      '@type': 'ListItem',
      position: 2,
      name: 'Documentation',
      item: absoluteUrl('/docs'),
    },
    ...page.slugs.map((_, index) => {
      const slugs = page.slugs.slice(0, index + 1);
      const breadcrumbPage = source.getPage(slugs);

      return {
        '@type': 'ListItem',
        position: index + 3,
        name:
          breadcrumbPage?.data.title ??
          slugs.at(-1)?.replaceAll('-', ' ') ??
          page.data.title,
        item: absoluteUrl(`/docs/${slugs.join('/')}`),
      };
    }),
  ];
  const breadcrumbList = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: breadcrumbs,
  };
  const techArticle = {
    '@context': 'https://schema.org',
    '@type': 'TechArticle',
    headline: page.data.title,
    description,
    url: absoluteUrl(page.url),
    mainEntityOfPage: absoluteUrl(page.url),
    image: absoluteUrl(getPageImage(page).url),
    inLanguage: 'en',
    isAccessibleForFree: true,
    license: licenseUrl,
    author: {
      '@type': 'Person',
      name: 'Piyush Gambhir',
      url: 'https://github.com/piyush-gambhir',
    },
    isPartOf: {
      '@type': 'WebSite',
      name: site.name,
      url: siteUrl,
      sameAs: [repositoryUrl],
    },
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: serializeJsonLd(breadcrumbList) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: serializeJsonLd(techArticle) }}
      />
      <DocsPage
        toc={page.data.toc}
        full={page.data.full}
        className="reckon-docs-page"
        tableOfContent={{ container: lenisPrevent }}
        tableOfContentPopover={{ content: lenisPrevent }}
      >
        <DocsTitle className="reckon-docs-title">{page.data.title}</DocsTitle>
        <DocsDescription className="reckon-docs-description mb-0">
          {page.data.description}
        </DocsDescription>
        <div className="reckon-docs-actions flex flex-row gap-2 items-center pb-6">
          <MarkdownCopyButton markdownUrl={markdownUrl} />
          <ViewOptionsPopover
            markdownUrl={markdownUrl}
            githubUrl={`https://github.com/${gitConfig.user}/${gitConfig.repo}/blob/${gitConfig.branch}/content/docs/${page.path}`}
          />
        </div>
        <DocsBody className="reckon-docs-body">
          <MDX
            components={getMDXComponents({
              // this allows you to link to other pages with relative file paths
              a: createRelativeLink(source, page),
            })}
          />
        </DocsBody>
      </DocsPage>
    </>
  );
}

export async function generateStaticParams() {
  return source.generateParams();
}

export async function generateMetadata(props: PageProps<'/docs/[[...slug]]'>): Promise<Metadata> {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  return createPageMetadata({
    title: page.data.title,
    description: describePage(
      page.data.description ?? `Technical documentation for ${page.data.title}.`,
    ),
    path: page.url,
    type: 'article',
    image: getPageImage(page).url,
  });
}

function serializeJsonLd(value: object): string {
  return JSON.stringify(value).replace(/</g, '\\u003c');
}
