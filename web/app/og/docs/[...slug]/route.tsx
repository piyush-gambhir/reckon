import { getPageImage, source } from '@/lib/source';
import { notFound } from 'next/navigation';
import { ImageResponse } from 'next/og';
import { site } from '@/lib/site';

export const revalidate = false;

import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const fontBuffer = async (name: string) => {
  const data = await readFile(join(process.cwd(), 'fonts', name));
  return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
};

const hafferDisplay = fontBuffer('haffer-xh-regular-2.ttf');
const hafferMono = fontBuffer('haffer-mono-medium-2.ttf');

export async function GET(_req: Request, { params }: RouteContext<'/og/docs/[...slug]'>) {
  const { slug } = await params;
  const page = source.getPage(slug.slice(0, -1));
  if (!page) notFound();

  const [displayFont, monoFont] = await Promise.all([hafferDisplay, hafferMono]);

  return new ImageResponse(
    <div
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '68px 76px',
        color: '#f3f4f1',
        background: '#131412',
        fontFamily: 'Haffer Display',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          fontFamily: 'Haffer Mono',
          fontSize: 24,
        }}
      >
        <span style={{ color: '#cdec4e' }}>&gt;_ {site.binary}</span>
        <span style={{ color: '#7f827b' }}>docs / {page.slugs.join(' / ') || 'introduction'}</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <div
          style={{
            maxWidth: 990,
            fontSize: 86,
            lineHeight: 0.96,
            letterSpacing: '-0.055em',
          }}
        >
          {page.data.title}
        </div>
        <div
          style={{
            maxWidth: 900,
            color: '#b6b8b3',
            fontFamily: 'Haffer Mono',
            fontSize: 26,
            lineHeight: 1.3,
          }}
        >
          {page.data.description}
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          width: '100%',
          height: 16,
          borderRadius: 8,
          background: '#cdec4e',
        }}
      />
    </div>,
    {
      width: 1200,
      height: 630,
      fonts: [
        {
          name: 'Haffer Display',
          data: displayFont,
          weight: 400,
          style: 'normal',
        },
        {
          name: 'Haffer Mono',
          data: monoFont,
          weight: 500,
          style: 'normal',
        },
      ],
    },
  );
}

export function generateStaticParams() {
  return source.getPages().map((page) => ({
    lang: page.locale,
    slug: getPageImage(page).segments,
  }));
}
