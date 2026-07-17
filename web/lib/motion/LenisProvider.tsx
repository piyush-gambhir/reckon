'use client';

import Lenis from 'lenis';
import { type ReactNode, useEffect } from 'react';
import { ScrollTrigger } from './gsap';

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

export function LenisProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    const root = document.documentElement;
    const reducedMotion = window.matchMedia(REDUCED_MOTION_QUERY);
    let lenis: Lenis | null = null;
    let bootObserver: MutationObserver | null = null;

    const onAnchorClick = (event: MouseEvent) => {
      if (
        !lenis ||
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }

      const anchor = (event.target as Element | null)?.closest<HTMLAnchorElement>(
        'a[href*="#"]',
      );
      if (
        !anchor ||
        anchor.target === '_blank' ||
        anchor.hasAttribute('download')
      ) {
        return;
      }

      const destination = new URL(anchor.href, window.location.href);
      if (
        destination.origin !== window.location.origin ||
        destination.pathname !== window.location.pathname ||
        destination.search !== window.location.search ||
        !destination.hash
      ) {
        return;
      }

      const target = document.getElementById(
        decodeURIComponent(destination.hash.slice(1)),
      );
      if (!target) return;

      event.preventDefault();
      window.history.pushState(null, '', destination.hash);
      lenis.scrollTo(target);
    };

    const destroyLenis = () => {
      bootObserver?.disconnect();
      bootObserver = null;
      document.removeEventListener('click', onAnchorClick);
      if (!lenis) return;
      lenis.off('scroll', ScrollTrigger.update);
      lenis.destroy();
      lenis = null;
    };

    const createLenis = () => {
      if (lenis || reducedMotion.matches || root.dataset.boot !== 'done') {
        return;
      }

      root.setAttribute('data-lenis', 'true');
      lenis = new Lenis({
        autoRaf: true,
        lerp: 0.165,
        wheelMultiplier: 1.25,
        prevent: (element) =>
          Boolean(
            element.closest(
              ".is--textarea:focus, [data-lenis-prevent], [data-radix-scroll-area-viewport], .fd-sidebar, #nd-sidebar, #nd-sidebar-mobile, [data-sidebar-panel], #nd-toc, [data-toc-popover-content], [role='dialog'], nav[aria-label='Primary navigation'] .nav-bar__bottom-inner",
            ),
          ),
      });
      lenis.on('scroll', ScrollTrigger.update);
      document.addEventListener('click', onAnchorClick);
      ScrollTrigger.refresh();
    };

    const syncMotionPreference = () => {
      destroyLenis();

      if (reducedMotion.matches) {
        root.removeAttribute('data-lenis');
        return;
      }

      if (root.dataset.boot === 'done') {
        createLenis();
        return;
      }

      bootObserver = new MutationObserver(() => {
        if (root.dataset.boot !== 'done') return;
        bootObserver?.disconnect();
        bootObserver = null;
        createLenis();
      });
      bootObserver.observe(root, {
        attributes: true,
        attributeFilter: ['data-boot'],
      });
    };

    syncMotionPreference();
    reducedMotion.addEventListener('change', syncMotionPreference);

    return () => {
      reducedMotion.removeEventListener('change', syncMotionPreference);
      destroyLenis();
      root.removeAttribute('data-lenis');
    };
  }, []);

  return children;
}
