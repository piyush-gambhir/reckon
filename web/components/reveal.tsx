'use client';

import {
  useRef,
  type ElementType,
  type HTMLAttributes,
  type ReactNode,
} from 'react';
import { gsap } from '@/lib/motion/gsap';
import { useGsap } from '@/lib/motion/useGsap';
import { cn } from '@/lib/utils';

interface RevealProps extends Omit<HTMLAttributes<HTMLElement>, 'children'> {
  as?: ElementType;
  children: ReactNode;
  stagger?: boolean;
}

export function Reveal({
  as: Component = 'div',
  children,
  className,
  stagger = false,
  ...props
}: RevealProps) {
  const elementRef = useRef<HTMLElement>(null);

  useGsap(
    () => {
      const element = elementRef.current;
      if (!element) return;

      const children = Array.from(element.children).filter((child) =>
        child.hasAttribute('data-reveal-item'),
      );
      const targets = stagger && children.length > 0 ? children : [element];

      gsap.set(targets, {
        y: '2em',
        rotation: 0.001,
        autoAlpha: 0,
      });

      const tween = gsap.to(targets, {
        y: 0,
        rotation: 0.001,
        autoAlpha: 1,
        duration: 0.9,
        ease: 'home-default',
        stagger: stagger ? 0.075 : 0,
        scrollTrigger: {
          trigger: element,
          start: 'top 85%',
          once: true,
        },
      });

      const motionPreference = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      );
      const finishReveal = (event: MediaQueryListEvent) => {
        if (!event.matches) return;
        tween.progress(1).kill();
        gsap.set(targets, { clearProps: 'transform,opacity,visibility' });
      };

      motionPreference.addEventListener('change', finishReveal);
      return () => {
        motionPreference.removeEventListener('change', finishReveal);
      };
    },
    [stagger],
    elementRef,
  );

  return (
    <Component
      ref={elementRef}
      className={cn('reveal', className)}
      {...props}
    >
      {children}
    </Component>
  );
}
