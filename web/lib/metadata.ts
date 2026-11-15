import type { Metadata } from 'next';
import { site } from '@/lib/site';
import { siteUrl } from '@/lib/shared';

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

export function describePage(summary: string): string {
  return `${summary.trim()} Works with Claude Code and Codex.`;
}

export interface PageMetadataOptions {
  title: string;
  description: string;
  socialDescription?: string;
  path: string;
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
