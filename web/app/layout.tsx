import type { Metadata } from 'next';
import '@fontsource-variable/inter';
import '@fontsource-variable/jetbrains-mono';
import '@fontsource/instrument-serif';
import type { CSSProperties } from 'react';
import { BootSequence } from '@/components/boot-sequence';
import { Provider } from '@/components/provider';
import { defaultSocialImage } from '@/lib/metadata';
import { LenisProvider } from '@/lib/motion/LenisProvider';
import {
  site,
  siteMetadataDescription,
} from '@/lib/site';
import { siteUrl } from '@/lib/shared';
import './global.css';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  applicationName: site.name,
  title: {
    default: `${site.name}: ${site.tagline}`,
    template: `%s · ${site.name}`,
  },
  description: siteMetadataDescription,
  alternates: {
    canonical: siteUrl,
  },
  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: siteUrl,
    siteName: site.name,
    title: `${site.name}: ${site.tagline}`,
    description: siteMetadataDescription,
    images: [
      {
        url: `${siteUrl}${defaultSocialImage}`,
        width: 1200,
        height: 630,
        alt: `${site.name} documentation`,
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    title: `${site.name}: ${site.tagline}`,
    description: siteMetadataDescription,
    images: [
      {
        url: `${siteUrl}${defaultSocialImage}`,
        width: 1200,
        height: 630,
        alt: `${site.name} documentation`,
      },
    ],
  },
  icons: {
    icon: [{ url: '/reckon/favicon.svg', type: 'image/svg+xml' }],
  },
};

export default function Layout({ children }: LayoutProps<'/'>) {
  const rootStyle = {
    ...(site.accent ? { '--site-accent': site.accent } : {}),
  } as CSSProperties;

  return (
    <html
      lang="en"
      data-accent={site.accentName}
      style={rootStyle}
      suppressHydrationWarning
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var root=document.documentElement;root.classList.add('js');try{var reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;var played=sessionStorage.getItem('reckon:boot-sequence-complete')==='true';root.dataset.boot=reduced||played?'done':'pending';}catch(error){root.dataset.boot='pending';}})();`,
          }}
        />
      </head>
      <body className="flex flex-col min-h-screen">
        <Provider>
          <BootSequence />
          <LenisProvider>{children}</LenisProvider>
        </Provider>
      </body>
    </html>
  );
}
