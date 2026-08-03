'use client';

import { useRef } from 'react';
import { ArrowRight } from 'lucide-react';
import { InstallCommand } from '@/components/install-command';
import { OsmoButton } from '@/components/ui/osmo-button';
import { gsap } from '@/lib/motion/gsap';
import { useGsap } from '@/lib/motion/useGsap';
import { site } from '@/lib/site';

/**
 * A viewport-locked stage with one centered idea: the headline, what it does,
 * and the two ways in. Nothing sits behind the type.
 */
export function HomeHero() {
  const rootRef = useRef<HTMLElement>(null);
  const words = site.tagline.split(/\s+/);
  // Deliberate two-line break: the long clause reads first, the answer lands
  // second. Accent stays on the closing pair.
  const opening = words.slice(0, -3);
  const closing = words.slice(-3);
  const repoUrl = `https://github.com/${site.repo}`;

  useGsap(
    () => {
      const root = rootRef.current;
      if (!root) return;

      const description = root.querySelector<HTMLElement>(
        '[data-hero-description]',
      );
      const cue = root.querySelector<HTMLElement>('[data-hero-cue]');
      const motionPreference = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      );
      let revealTimeline: gsap.core.Timeline | null = null;
      let bootObserver: MutationObserver | null = null;
      let bootFrame = 0;

      const revealHero = () => {
        if (revealTimeline) return;

        revealTimeline = gsap
          .timeline({ defaults: { ease: 'expo.out' } })
          .from(description, { y: '2em', duration: 1.2 }, 0)
          .from(cue, { autoAlpha: 0, y: '1em', duration: 0.9 }, 0.35);
      };

      const html = document.documentElement;
      if (html.dataset.boot === 'done') {
        revealHero();
      } else {
        bootObserver = new MutationObserver(() => {
          if (html.dataset.boot !== 'done') return;
          bootObserver?.disconnect();
          revealHero();
        });
        bootObserver.observe(html, {
          attributes: true,
          attributeFilter: ['data-boot'],
        });
        bootFrame = window.requestAnimationFrame(() => {
          const bootIsActive =
            document.body.classList.contains('booting') ||
            Boolean(document.querySelector('[data-boot-sequence]'));
          if (html.dataset.boot === 'done' || !bootIsActive) {
            bootObserver?.disconnect();
            revealHero();
          }
        });
      }

      const handlePreferenceChange = (event: MediaQueryListEvent) => {
        if (!event.matches) return;
        bootObserver?.disconnect();
        revealTimeline?.progress(1).kill();
        gsap.set([description, cue], {
          clearProps: 'transform,opacity,visibility',
        });
      };

      motionPreference.addEventListener('change', handlePreferenceChange);
      return () => {
        motionPreference.removeEventListener('change', handlePreferenceChange);
        window.cancelAnimationFrame(bootFrame);
        bootObserver?.disconnect();
        revealTimeline?.kill();
      };
    },
    [],
    rootRef,
  );

  return (
    <section ref={rootRef} className="osmo-home-hero">
      {/* Gutter ticks: the stage admits it sits on a grid. */}
      <span className="osmo-home-hero__tick is--start" aria-hidden="true">
        +
      </span>
      <span className="osmo-home-hero__tick is--end" aria-hidden="true">
        +
      </span>

      <div className="osmo-container osmo-home-hero__inner">
        <h1 className="osmo-home-hero__title">
          <span className="home-motion__text-mask">
            <span className="home-motion__text-line">{opening.join(' ')}</span>
          </span>
          <span className="home-motion__text-mask">
            <span className="home-motion__text-line">
              {`${closing[0]} `}
              {/* The answer the terminal prints, in the terminal's color. */}
              <span className="osmo-home-hero__accent">
                {closing[1]}{' '}
                <span className="osmo-home-hero__tail">
                  {closing[2]}
                  <span className="osmo-home-hero__cursor" aria-hidden="true" />
                </span>
              </span>
            </span>
          </span>
        </h1>

        <p className="osmo-home-hero__description" data-hero-description>
          {site.description}
        </p>

        <div className="osmo-home-hero__actions">
          <OsmoButton href="/docs" aria-label="Get started" icon={<ArrowRight />}>
            Get started
          </OsmoButton>
          <OsmoButton href={repoUrl} theme="neutral" aria-label="View on GitHub">
            View on GitHub
          </OsmoButton>
        </div>

        <div className="osmo-home-hero__install">
          <p className="reckon-scribble osmo-home-hero__scribble">
            read-only, always
          </p>
          <InstallCommand command={site.installCommand} />
        </div>
      </div>

      <a className="osmo-home-hero__cue" href="#stack" data-hero-cue>
        <span aria-hidden="true">&darr;</span>
        Scroll to explore
        <span aria-hidden="true">&darr;</span>
      </a>
    </section>
  );
}
