export const dynamic = 'force-static';
import type { MetadataRoute } from 'next';
import { source } from '@/lib/source';
import { siteUrl } from '@/lib/shared';

export default function sitemap(): MetadataRoute.Sitemap {
  const routes = ['/', ...source.getPages().map((page) => page.url), '/privacy', '/terms', '/contact'];

  return [...new Set(routes)].map((route) => ({
    url: `${siteUrl}${route}`,
    changeFrequency: route === '/' ? 'weekly' : 'monthly',
    priority: route === '/' ? 1 : route.startsWith('/docs') ? 0.8 : 0.4,
  }));
}
