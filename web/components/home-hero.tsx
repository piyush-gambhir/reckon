'use client';

import { Fragment, useRef } from 'react';
import { ArrowRight } from 'lucide-react';
import { HeroTerminal } from '@/components/hero-terminal';
import { InstallCommand } from '@/components/install-command';
import { Reveal } from '@/components/reveal';
import { OsmoButton } from '@/components/ui/osmo-button';
import { gsap, ScrollTrigger } from '@/lib/motion/gsap';
import { useGsap } from '@/lib/motion/useGsap';
import { site } from '@/lib/site';

export function HomeHero() {
  const rootRef = useRef<HTMLElement>(null);
  const taglineWords = site.tagline.split(/\s+/);
  const repoUrl = `https://github.com/${site.repo}`;

  useGsap(
    () => {
      const root = rootRef.current;
      if (!root) return;

      const words = gsap.utils.toArray<HTMLElement>('[data-hero-word]', root);
      const description = root.querySelector<HTMLElement>(
        '[data-hero-description]',
      );
      const blobs = gsap.utils.toArray<HTMLElement>('[data-hero-aurora]', root);
      const motionPreference = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      );
      let revealTimeline: gsap.core.Timeline | null = null;
      let bootObserver: MutationObserver | null = null;
      let bootFrame = 0;

      const revealHero = () => {
        if (revealTimeline) return;

        gsap.set(words, {
          yPercent: 100,
          rotation: 10,
          transformOrigin: 'bottom left',
        });

        revealTimeline = gsap
          .timeline({ defaults: { ease: 'expo.out' } })
          .to(
            words,
            {
              yPercent: 0,
              rotation: 0,
              autoAlpha: 1,
              duration: 1.2,
              stagger: 0.05,
            },
            0,
          )
          .from(description, { y: '2em', duration: 1.2 }, 0);
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

      const blobTweens = blobs.map((blob, index) =>
        gsap.to(blob, {
          xPercent: index === 0 ? 6 : -6,
          yPercent: index === 0 ? 4 : -4,
          duration: 8,
          ease: 'sine.inOut',
          yoyo: true,
          repeat: -1,
        }),
      );

      // Subtle parallax: the terminal drifts up slightly as the hero scrolls
      // out, giving the stage depth without competing with the typing.
      const terminalStage = root.querySelector<HTMLElement>(
        '[data-hero-terminal-stage]',
      );
      const parallaxTween = terminalStage
        ? gsap.to(terminalStage, {
            yPercent: -6,
            ease: 'none',
            scrollTrigger: {
              trigger: root,
              start: 'top top',
              end: 'bottom top',
              scrub: true,
            },
          })
        : null;

      const visibilityTrigger = ScrollTrigger.create({
        trigger: root,
        start: 'top bottom',
        end: 'bottom top',
        onEnter: () => blobTweens.forEach((tween) => tween.resume()),
        onEnterBack: () => blobTweens.forEach((tween) => tween.resume()),
        onLeave: () => blobTweens.forEach((tween) => tween.pause()),
        onLeaveBack: () => blobTweens.forEach((tween) => tween.pause()),
      });

      const handlePreferenceChange = (event: MediaQueryListEvent) => {
        if (!event.matches) return;
        bootObserver?.disconnect();
        revealTimeline?.progress(1).kill();
        blobTweens.forEach((tween) => tween.kill());
        parallaxTween?.scrollTrigger?.kill();
        parallaxTween?.kill();
        if (terminalStage) gsap.set(terminalStage, { clearProps: 'transform' });
        gsap.set([...words, description, ...blobs], {
          clearProps: 'transform,opacity,visibility',
        });
      };

      motionPreference.addEventListener('change', handlePreferenceChange);
      return () => {
        motionPreference.removeEventListener('change', handlePreferenceChange);
        window.cancelAnimationFrame(bootFrame);
        bootObserver?.disconnect();
        visibilityTrigger.kill();
        revealTimeline?.kill();
        parallaxTween?.scrollTrigger?.kill();
        parallaxTween?.kill();
        blobTweens.forEach((tween) => tween.kill());
      };
    },
    [],
    rootRef,
  );

  return (
    <section ref={rootRef} className="osmo-home-hero">
      <div className="osmo-home-hero__aurora" aria-hidden="true">
        <span
          className="osmo-home-hero__aurora-blob is--electric"
          data-hero-aurora
        />
        <span
          className="osmo-home-hero__aurora-blob is--purple"
          data-hero-aurora
        />
      </div>
      <div className="osmo-container osmo-home-hero__inner">
        <div className="osmo-home-hero__grid">
          <div className="osmo-home-hero__content">
        <h1 className="osmo-home-hero__title" aria-label={site.tagline}>
          <span className="home-motion__text-mask" aria-hidden="true">
            <span className="home-motion__text-line">
              {taglineWords.slice(0, -2).map((word, index) => (
                <Fragment key={`${word}-${index}`}>
                  <span data-hero-word>{word}</span>{' '}
                </Fragment>
              ))}
              {/* The answer the terminal prints, in the terminal's color. */}
              <span className="osmo-home-hero__accent">
                <span data-hero-word>
                  {taglineWords[taglineWords.length - 2]}
                </span>{' '}
                <span className="osmo-home-hero__tail">
                  <span data-hero-word>
                    {taglineWords[taglineWords.length - 1]}
                  </span>
                  <span className="osmo-home-hero__cursor" />
                </span>
              </span>
            </span>
          </span>
        </h1>
        <p className="osmo-home-hero__description" data-hero-description>
          {site.description}
        </p>

        <div className="osmo-home-hero__actions">
          <OsmoButton
            href="/docs"
            aria-label="Get started"
            icon={<ArrowRight />}
          >
            Get started
          </OsmoButton>
          <OsmoButton
            href={repoUrl}
            theme="neutral"
            aria-label="View on GitHub"
          >
            View on GitHub
          </OsmoButton>
        </div>
        <div className="osmo-home-hero__install">
          <InstallCommand command={site.installCommand} />
        </div>
          </div>

          <Reveal
            className="osmo-home-hero__terminal-stage"
            data-hero-terminal-stage
          >
            <p className="reckon-scribble osmo-home-hero__scribble">
              read-only, always
            </p>
            <div className="osmo-home-hero__terminal">
              <HeroTerminal title={site.exampleTitle} command={site.example} />
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
