import type { Metadata } from 'next';
import { site } from '@/lib/site';
import { siteUrl } from '@/lib/shared';

export const defaultSocialImage = '/og/docs/image.png';

export function absoluteUrl(path: string): string {
  return `${siteUrl}${path}`;
}

export function createPageMetadata({
  title,
  summary,
  path,
  type = 'website',
  image = defaultSocialImage,
}: {
  title: string;
  summary: string;
  path: string;
  type?: 'article' | 'website';
  image?: string;
}): Metadata {
  const description = `${summary} Any shell-capable agent can use it.`;
  const socialTitle = `${title} · ${site.name}`;
  const canonicalUrl = `${siteUrl}${path}`;
  const socialImage = {
    url: `${siteUrl}${image}`,
    width: 1200,
    height: 630,
    alt: `${title} on ${site.name}`,
  };
  const sharedOpenGraph = {
    title: socialTitle,
    description,
    url: canonicalUrl,
    siteName: site.name,
    locale: 'en_US',
    images: [socialImage],
  };

  return {
    title,
    description,
    alternates: {
      canonical: canonicalUrl,
    },
    openGraph:
      type === 'article'
        ? { ...sharedOpenGraph, type: 'article' }
        : { ...sharedOpenGraph, type: 'website' },
    twitter: {
      card: 'summary_large_image',
      title: socialTitle,
      description,
      images: [socialImage],
    },
  };
}
