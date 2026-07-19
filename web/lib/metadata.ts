import type { Metadata } from 'next';
import { site } from '@/lib/site';
import { siteUrl } from '@/lib/shared';

/** Social card for the site root. Rendered by app/og/home/image.png/route.tsx. */
export const homeSocialImage = {
  url: `${siteUrl}/og/home/image.png`,
  width: 1200,
  height: 630,
  alt: `${site.name}: ${site.tagline}`,
};


export const defaultSocialImage = '/og/docs/image.png';

export function absoluteUrl(path: string): string {
  return `${siteUrl}${path}`;
}

/** Expands a short page summary into this site's full description voice. */
export function describePage(summary: string): string {
  return `${summary.trim()} Any shell-capable agent can use it.`;
}

export interface PageMetadataOptions {
  /** Page title. Used bare for <title>, suffixed with the site name socially. */
  title: string;
  /** Page description, used for <meta name="description">. */
  description: string;
  /** Optional distinct description for OG/Twitter. Defaults to `description`. */
  socialDescription?: string;
  /** Site-relative path, e.g. '/docs/quickstart'. */
  path: string;
  /** Site-relative social image path. */
  image?: string;
  type?: 'article' | 'website';
}

export function createPageMetadata({
  title,
  description,
  socialDescription,
  path,
  image = defaultSocialImage,
  type = 'website',
}: PageMetadataOptions): Metadata {
  const social = socialDescription?.trim() || description;
  const socialTitle = `${title} · ${site.name}`;
  const canonicalUrl = absoluteUrl(path);
  const socialImage = {
    url: absoluteUrl(image),
    width: 1200,
    height: 630,
    alt: `${title} on ${site.name}`,
  };

  return {
    title,
    description,
    alternates: {
      canonical: canonicalUrl,
    },
    openGraph: {
      type,
      url: canonicalUrl,
      siteName: site.name,
      locale: 'en_US',
      title: socialTitle,
      description: social,
      images: [socialImage],
    },
    twitter: {
      card: 'summary_large_image',
      title: socialTitle,
      description: social,
      images: [socialImage],
    },
  };
}
