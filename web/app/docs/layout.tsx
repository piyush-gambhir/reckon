import { source } from '@/lib/source';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { baseOptions } from '@/lib/layout.shared';
import type { ComponentProps } from 'react';

const lenisPreventSidebar = {
  'data-lenis-prevent': '',
} as ComponentProps<typeof DocsLayout>['sidebar'];

export default function Layout({ children }: LayoutProps<'/docs'>) {
  return (
    <DocsLayout
      tree={source.getPageTree()}
      containerProps={{ className: 'reckon-docs-shell' }}
      {...baseOptions()}
      sidebar={lenisPreventSidebar}
    >
      {children}
    </DocsLayout>
  );
}
