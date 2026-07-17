'use client';

import { useRef } from 'react';
import { ArrowRight } from 'lucide-react';
import { InstallCommand } from '@/components/install-command';
import { OsmoButton } from '@/components/ui/osmo-button';
import { gsap } from '@/lib/motion/gsap';
import { useGsap } from '@/lib/motion/useGsap';
import { site } from '@/lib/site';

export function CtaBand() {
  const rootRef = useRef<HTMLDivElement>(null);

  useGsap(
    () => {
      const root = rootRef.current;
      const field = root?.querySelector<HTMLElement>('[data-cta-field]');
      if (!root || !field) return;

      const finePointer = window.matchMedia(
        '(hover: hover) and (pointer: fine)',
      );
      const reducedMotion = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      );
      let listening = false;

      const resetField = () => gsap.set(field, { x: 0, y: 0 });
      const moveField = (event: PointerEvent) => {
        const bounds = root.getBoundingClientRect();
        const x = (event.clientX - bounds.left) / bounds.width - 0.5;
        const y = (event.clientY - bounds.top) / bounds.height - 0.5;
        gsap.set(field, { x: `${x}em`, y: `${y}em` });
      };

      const startListening = () => {
        if (listening || !finePointer.matches || reducedMotion.matches) return;
        listening = true;
        root.addEventListener('pointermove', moveField);
        root.addEventListener('pointerleave', resetField);
      };

      const stopListening = () => {
        if (!listening) return;
        listening = false;
        root.removeEventListener('pointermove', moveField);
        root.removeEventListener('pointerleave', resetField);
        resetField();
      };

      const syncPointer = () => {
        stopListening();
        startListening();
      };

      startListening();
      finePointer.addEventListener('change', syncPointer);
      reducedMotion.addEventListener('change', syncPointer);

      return () => {
        finePointer.removeEventListener('change', syncPointer);
        reducedMotion.removeEventListener('change', syncPointer);
        stopListening();
      };
    },
    [],
    rootRef,
  );

  return (
    <div ref={rootRef} className="osmo-cta-card">
      <div className="osmo-cta-card__field" data-cta-field aria-hidden="true">
        RECKON
      </div>
      <p className="reckon-scribble osmo-cta-card__scribble">
        we&apos;ll see you on-call
      </p>
      <div className="osmo-cta-card__content">
        <h2 className="osmo-cta-card__title">Ready in one command</h2>
        <p className="osmo-cta-card__body">
          {site.ctaBody ??
            'Install the binary, authenticate, and start querying. No runtime, no dependencies.'}
        </p>
        <InstallCommand
          command={site.installCommand}
          className="osmo-cta-card__install"
        />
        <OsmoButton
          href="/docs"
          className="osmo-cta-card__action"
          icon={<ArrowRight />}
        >
          Read the docs
        </OsmoButton>
      </div>
    </div>
  );
}
