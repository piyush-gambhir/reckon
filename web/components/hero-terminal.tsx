'use client';

import { useRef } from 'react';
import { gsap } from '@/lib/motion/gsap';
import { useGsap } from '@/lib/motion/useGsap';
import { cn } from '@/lib/utils';

function Line({ line }: { line: string }) {
  if (line.trim() === '') return <span>{'\n'}</span>;

  // Comment line
  if (line.trimStart().startsWith('#')) {
    return <span className="hero-terminal__comment">{line}</span>;
  }

  const tokens = line.split(/(\s+)/);
  let seenBinary = false;

  return (
    <span>
      {tokens.map((tok, i) => {
        if (/^\s+$/.test(tok)) return <span key={i}>{tok}</span>;

        // first non-space token = the binary
        if (!seenBinary) {
          seenBinary = true;
          return (
            <span key={i} className="hero-terminal__binary">
              {tok}
            </span>
          );
        }
        if (tok.startsWith('-')) {
          return (
            <span key={i} className="hero-terminal__flag">
              {tok}
            </span>
          );
        }
        if (
          /^["'].*["']$/.test(tok) ||
          tok.startsWith('"') ||
          tok.startsWith("'")
        ) {
          return (
            <span key={i} className="hero-terminal__string">
              {tok}
            </span>
          );
        }
        return (
          <span key={i}>
            {tok}
          </span>
        );
      })}
    </span>
  );
}

export function HeroTerminal({
  title,
  command,
  className,
}: {
  title: string;
  command: string;
  className?: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const lines = command.split('\n');

  useGsap(
    () => {
      const root = rootRef.current;
      if (!root) return;

      const terminalLines = gsap.utils.toArray<HTMLElement>(
        '[data-terminal-line]',
        root,
      );
      const spinner = root.querySelector<HTMLElement>(
        '[data-terminal-spinner]',
      );

      gsap.set(terminalLines, { yPercent: 100, autoAlpha: 0 });
      gsap.set(spinner, { display: 'block' });

      const timeline = gsap.timeline({
        scrollTrigger: {
          trigger: root,
          start: 'top 85%',
          once: true,
        },
        onComplete: () => gsap.set(spinner, { display: 'none' }),
      });

      timeline.to(
        terminalLines,
        {
          yPercent: 0,
          autoAlpha: 1,
          duration: 1.2,
          stagger: 0.05,
          ease: 'expo.out',
        },
        0.9,
      );

      const motionPreference = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      );
      const finishScan = (event: MediaQueryListEvent) => {
        if (!event.matches) return;
        timeline.progress(1).kill();
        gsap.set(terminalLines, {
          clearProps: 'transform,opacity,visibility',
        });
        gsap.set(spinner, { display: 'none' });
      };

      motionPreference.addEventListener('change', finishScan);
      return () => {
        motionPreference.removeEventListener('change', finishScan);
      };
    },
    [],
    rootRef,
  );

  return (
    <div ref={rootRef} className={cn('hero-terminal', className)}>
      <div className="hero-terminal__titlebar">
        <span className="hero-terminal__dots" aria-hidden="true">
          <span className="hero-terminal__dot is--red" />
          <span className="hero-terminal__dot is--amber" />
          <span className="hero-terminal__dot is--emerald" />
        </span>
        <span className="osmo-eyebrow">{title}</span>
        <span
          className="hero-terminal__spinner"
          data-terminal-spinner
          aria-hidden="true"
        />
      </div>
      <pre className="hero-terminal__body">
        <code>
          {lines.map((line, i) => (
            <span
              className="home-motion__text-mask hero-terminal__line-mask"
              key={i}
            >
              <span
                className="home-motion__text-line hero-terminal__line"
                data-terminal-line
              >
                {!line.trimStart().startsWith('#') && line.trim() !== '' ? (
                  <span className="hero-terminal__prompt" aria-hidden="true">
                    $
                  </span>
                ) : null}
                <Line line={line} />
              </span>
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}
