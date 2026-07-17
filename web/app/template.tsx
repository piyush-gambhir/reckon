'use client';

import { usePathname } from 'next/navigation';
import { useRef, type ReactNode } from 'react';
import { gsap } from '@/lib/motion/gsap';
import { useGsap } from '@/lib/motion/useGsap';

export default function Template({ children }: { children: ReactNode }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();

  useGsap(
    () => {
      const root = rootRef.current;
      if (!root) return;

      const isDocsRoute = pathname.startsWith('/docs');
      const tween = gsap.fromTo(
        root,
        {
          autoAlpha: 0,
          y: isDocsRoute ? 0 : '1.5em',
        },
        {
          autoAlpha: 1,
          y: 0,
          duration: 0.6,
          ease: 'home-default',
          clearProps: 'transform,opacity,visibility',
        },
      );

      return () => tween.kill();
    },
    [pathname],
    rootRef,
  );

  return (
    <div
      ref={rootRef}
      className="route-entry"
      data-route-kind={pathname.startsWith('/docs') ? 'docs' : 'default'}
    >
      {children}
    </div>
  );
}
