'use client';

import { useLayoutEffect, useRef } from 'react';
import { gsap } from '@/lib/motion/gsap';

const BOOT_STORAGE_KEY = 'reckon:boot-sequence-complete';
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

const bootLines = [
  'wiring read-only toolbelt…',
  'grafana · jenkins · cubeapm · aws · gh · k8s · kafka · redis · dbs',
  'scanning signals…',
  'ready.',
] as const;

export function BootSequence() {
  const overlayRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;

    const root = document.documentElement;
    const body = document.body;
    const reducedMotion = window.matchMedia(REDUCED_MOTION_QUERY);
    let complete = false;

    const hasPlayed = () => {
      try {
        return window.sessionStorage.getItem(BOOT_STORAGE_KEY) === 'true';
      } catch {
        return false;
      }
    };

    const markPlayed = () => {
      try {
        window.sessionStorage.setItem(BOOT_STORAGE_KEY, 'true');
      } catch {
        // Storage can be unavailable in privacy-restricted contexts.
      }
    };

    const finish = (hideImmediately = false) => {
      if (complete) return;
      complete = true;
      markPlayed();
      body.classList.remove('booting');
      root.dataset.boot = 'done';
      if (hideImmediately) gsap.set(overlay, { display: 'none' });
    };

    if (reducedMotion.matches || hasPlayed() || root.dataset.boot === 'done') {
      finish(true);
      return;
    }

    root.dataset.boot = 'running';
    body.classList.add('booting');
    gsap.set(overlay, { display: 'grid', yPercent: 0 });

    const lines = gsap.utils.toArray<HTMLElement>(
      '[data-boot-line]',
      overlay,
    );
    const timeline = gsap.timeline({
      onComplete: () => {
        finish();
        gsap.set(overlay, { display: 'none' });
      },
    });

    // The first line reveals via a pure-CSS animation the moment the overlay
    // paints (no hydration dependency), so the plate is never blank. GSAP
    // sequences the remaining lines like a real boot log.
    const queuedLines = lines.slice(1);
    gsap.set(queuedLines, { yPercent: 100, autoAlpha: 0 });
    timeline
      .to(queuedLines, {
        yPercent: 0,
        autoAlpha: 1,
        duration: 0.5,
        ease: 'expo.out',
        stagger: 0.45,
      }, 0.45)
      .to(
        overlay,
        {
          yPercent: -100,
          duration: 0.8,
          ease: 'home-default',
        },
        2.15,
      );

    const skipAnimation = (event: MediaQueryListEvent) => {
      if (!event.matches) return;
      timeline.kill();
      finish(true);
    };

    reducedMotion.addEventListener('change', skipAnimation);
    return () => {
      reducedMotion.removeEventListener('change', skipAnimation);
      timeline.kill();
      body.classList.remove('booting');
    };
  }, []);

  return (
    <div
      ref={overlayRef}
      className="boot-sequence"
      data-boot-sequence=""
      role="status"
      aria-label="Reckon is ready"
    >
      <div className="boot-sequence__log" aria-hidden="true">
        {bootLines.map((line, index) => (
          <span className="home-motion__text-mask" key={line}>
            <span
              className={`home-motion__text-line boot-sequence__line${index === 0 ? ' is--first' : ''}`}
              data-boot-line=""
            >
              <span className="boot-sequence__prompt">&gt;</span>
              <span>{line}</span>
              {line === 'scanning signals…' ? (
                <span className="loading-icon boot-sequence__spinner">
                  <span className="loading-icon__inner" />
                </span>
              ) : null}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
