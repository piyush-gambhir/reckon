'use client';

import { useRef } from 'react';
import { Reveal } from '@/components/reveal';
import { gsap } from '@/lib/motion/gsap';
import { useGsap } from '@/lib/motion/useGsap';

const investigationSteps = [
  {
    title: 'Assess',
    snippet: 'grafana alert rule list -o json',
    body: 'Check Grafana alerts, Jenkins build status, and the CubeAPM service inventory.',
  },
  {
    title: 'Investigate errors',
    snippet: 'cubeapm traces search --status error --last 1h',
    body: 'Search error traces and related logs, then open the trace waterfall for the affected service.',
  },
  {
    title: 'Check metrics',
    snippet: "cubeapm metrics query 'error_rate' --last 1h",
    body: 'Compare error rate, latency, and service health across the incident window.',
  },
  {
    title: 'Check deployments',
    snippet: 'jenkins build list checkout/deploy --limit 5',
    body: 'Review deploy annotations, recent Jenkins builds, build logs, merged pull requests, and releases.',
  },
  {
    title: 'Map dependencies',
    snippet: 'cubeapm traces get <trace-id> -o json',
    body: 'Trace error propagation through the dependency graph and check the supporting infrastructure.',
  },
  {
    title: 'Correlate',
    snippet: 'kubectl get events -n checkout --sort-by=.lastTimestamp',
    body: 'Match deployment timestamps with error spikes, Grafana annotations, and cluster events.',
  },
] as const;

export function RcaSteps() {
  const stepsRef = useRef<HTMLDivElement>(null);

  useGsap(
    () => {
      const root = stepsRef.current;
      const connector = root?.querySelector<HTMLElement>(
        '[data-steps-connector]',
      );
      if (!root || !connector) return;

      // Draw once when the section enters view. A scroll-scrubbed line parks
      // mid-draw at most scroll positions and reads as broken, so the full
      // draw plays a single time instead.
      gsap.set(connector, { scaleY: 0, transformOrigin: 'top' });
      const tween = gsap.to(connector, {
        scaleY: 1,
        duration: 1.2,
        ease: 'expo.out',
        scrollTrigger: {
          trigger: root,
          start: 'top 70%',
        },
      });

      const motionPreference = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      );
      const finishConnector = (event: MediaQueryListEvent) => {
        if (!event.matches) return;
        tween.progress(1).kill();
        gsap.set(connector, { clearProps: 'transform' });
      };

      motionPreference.addEventListener('change', finishConnector);
      return () => {
        motionPreference.removeEventListener('change', finishConnector);
      };
    },
    [],
    stepsRef,
  );

  return (
    <section
      className="osmo-section reckon-steps"
      aria-labelledby="reckon-steps-heading"
    >
      <div className="osmo-container">
        <Reveal className="osmo-section__header">
          <h2 id="reckon-steps-heading" className="osmo-section__title">
            How an investigation flows
          </h2>
        </Reveal>

        <div ref={stepsRef} className="reckon-steps__stage">
          <span
            className="reckon-steps__connector"
            data-steps-connector
            aria-hidden="true"
          />
          <p className="reckon-scribble reckon-steps__scribble">
            start broad, then narrow
          </p>
          <Reveal className="reckon-steps__list" stagger>
            {investigationSteps.map((step, index) => (
              <article
                className="reckon-step"
                data-reveal-item
                key={step.title}
              >
                <span className="osmo-eyebrow reckon-step__index">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <div className="reckon-step__copy">
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                  <code className="reckon-step__snippet">
                    <span aria-hidden>$ </span>
                    {step.snippet}
                  </code>
                </div>
              </article>
            ))}
          </Reveal>
        </div>
      </div>
    </section>
  );
}
