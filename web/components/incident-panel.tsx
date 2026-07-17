'use client';

import { useEffect, useRef } from 'react';
import { gsap } from '@/lib/motion/gsap';
import { useGsap } from '@/lib/motion/useGsap';

const signals = [
  {
    source: 'Grafana alert',
    command: 'grafana alert rule list -o json',
    finding: 'Establish the active alert window.',
  },
  {
    source: 'Jenkins build',
    command: 'jenkins job list --recursive --status FAILURE -o json',
    finding: 'Compare build start and completion times.',
  },
  {
    source: 'CubeAPM error rate',
    command:
      'cubeapm metrics query \'rate(http_requests_total{status=~"5.."}[5m])\' -o json',
    finding: 'Locate the error spike.',
  },
  {
    source: 'Kubernetes event',
    command:
      'kubectl get events -n checkout --sort-by=.lastTimestamp | tail -30',
    finding: 'Check whether the failure lines up with cluster events.',
  },
] as const;

export function IncidentPanel() {
  const rootRef = useRef<HTMLElement>(null);

  useGsap(
    () => {
      const root = rootRef.current;
      if (!root) return;

      const rows = root.querySelectorAll<HTMLElement>('[data-incident-signal]');
      const marker = root.querySelector<HTMLElement>(
        '.incident-panel__correlation-marker',
      );
      if (!rows.length || !marker) return;

      gsap.set(rows, { y: '2em', rotation: 0.001, autoAlpha: 0 });
      gsap.set(marker, { scaleX: 0, transformOrigin: 'left' });

      const timeline = gsap.timeline({
        scrollTrigger: {
          trigger: root,
          start: 'top 85%',
          once: true,
        },
      });

      timeline
        .to(rows, {
          y: 0,
          rotation: 0.001,
          autoAlpha: 1,
          duration: 0.9,
          ease: 'home-default',
          stagger: 0.075,
        })
        .to(marker, {
          scaleX: 1,
          duration: 0.9,
          ease: 'home-default',
        });

      const motionPreference = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      );
      const finishReveal = (event: MediaQueryListEvent) => {
        if (!event.matches) return;
        timeline.progress(1).kill();
        gsap.set(rows, { clearProps: 'transform,opacity,visibility' });
        gsap.set(marker, { clearProps: 'transform,transformOrigin' });
      };

      motionPreference.addEventListener('change', finishReveal);
      return () => {
        motionPreference.removeEventListener('change', finishReveal);
      };
    },
    [],
    rootRef,
  );

  useEffect(() => {
    const root = rootRef.current;
    const visual = root?.querySelector<HTMLElement>(
      '.incident-panel__visual',
    );
    const panel = visual?.querySelector<HTMLElement>(
      '.incident-panel__surface',
    );
    if (!root || !visual || !panel) return;

    const reducedMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    );
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
    const context = gsap.context(
      () => gsap.set(panel, { transformOrigin: 'center center' }),
      root,
    );

    const enter = () => {
      if (reducedMotion.matches || !finePointer.matches) return;
      gsap.to(panel, {
        scale: 1.01,
        duration: 0.2,
        ease: 'power1.out',
        overwrite: true,
      });
      gsap.to(visual, {
        boxShadow: '0 1.75em 4.5em rgb(32 29 29 / 16%)',
        duration: 0.2,
        ease: 'power1.out',
        overwrite: true,
      });
    };
    const leave = () => {
      gsap.to(panel, {
        scale: 1,
        duration: reducedMotion.matches ? 0 : 0.2,
        ease: 'power1.out',
        overwrite: true,
      });
      gsap.to(visual, {
        boxShadow: '0 1.5em 4em rgb(32 29 29 / 12%)',
        duration: reducedMotion.matches ? 0 : 0.2,
        ease: 'power1.out',
        overwrite: true,
      });
    };
    const resetForPreference = () => leave();

    visual.addEventListener('pointerenter', enter);
    visual.addEventListener('pointerleave', leave);
    reducedMotion.addEventListener('change', resetForPreference);
    finePointer.addEventListener('change', resetForPreference);

    return () => {
      visual.removeEventListener('pointerenter', enter);
      visual.removeEventListener('pointerleave', leave);
      reducedMotion.removeEventListener('change', resetForPreference);
      finePointer.removeEventListener('change', resetForPreference);
      gsap.killTweensOf([visual, panel]);
      context.revert();
    };
  }, []);

  return (
    <section
      ref={rootRef}
      className="osmo-section incident-panel"
      aria-labelledby="incident-panel-heading"
    >
      <div className="osmo-container">
        <div className="incident-panel__heading">
          <div>
            <h2 id="incident-panel-heading" className="incident-panel__title">
              Correlate the timeline
            </h2>
            <p className="incident-panel__description">
              Wire together twelve read-only CLIs in one isolated credential
              environment so a coding agent can correlate signals across systems
              in one session.
            </p>
          </div>
          <p className="reckon-scribble incident-panel__scribble">
            correlate the timestamps
          </p>
        </div>

        <div className="incident-panel__visual">
          <div className="incident-panel__surface">
            <div className="incident-panel__chrome">
              <span>incident / checkout</span>
              <span className="incident-panel__status">
                <span aria-hidden="true" /> correlation view
              </span>
            </div>

            <div className="incident-panel__axis" aria-hidden="true">
              <span>incident window</span>
              <span>deploy ↔ spike</span>
              <span className="incident-panel__correlation-track">
                <span className="incident-panel__correlation-marker" />
              </span>
            </div>

            <div className="incident-panel__signals">
              {signals.map((signal, index) => (
                <article
                  className="incident-signal"
                  data-incident-signal
                  key={signal.source}
                >
                  <div className="incident-signal__source">
                    <span>{String(index + 1).padStart(2, '0')}</span>
                    <strong>{signal.source}</strong>
                  </div>
                  <div className="incident-signal__time" aria-hidden="true">
                    <span />
                  </div>
                  <div className="incident-signal__evidence">
                    <code>{signal.command}</code>
                    <p>{signal.finding}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
