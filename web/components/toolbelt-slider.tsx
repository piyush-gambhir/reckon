'use client';

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';
import { gsap } from '@/lib/motion/gsap';

const tools = [
  {
    title: 'Grafana',
    sample: 'grafana alert rule list -o json',
    command: 'grafana',
    covers: 'Dashboards, datasources, alerts, and annotations.',
  },
  {
    title: 'Jenkins',
    sample: 'jenkins job list --recursive --status FAILURE',
    command: 'jenkins',
    covers: 'Jobs, builds, pipelines, logs, and nodes.',
  },
  {
    title: 'CubeAPM',
    sample: 'cubeapm traces search --service checkout',
    command: 'cubeapm',
    covers: 'Distributed traces, PromQL metrics, and LogsQL logs.',
  },
  {
    title: 'AWS',
    sample: 'aws logs tail /ecs/checkout --since 1h',
    command: 'aws',
    covers: 'CloudWatch metrics and logs, ALB/ELB, ECS, SQS, RDS, and S3.',
  },
  {
    title: 'gh',
    sample: 'gh run list --branch main --limit 20',
    command: 'gh',
    covers: 'Pull requests, GitHub Actions runs, releases, and issues.',
  },
  {
    title: 'Kafka',
    sample: 'rpk group describe checkout-consumers',
    command: 'kcat + rpk',
    covers: 'Metadata, group-less topic reads, consumer-group lag, and cluster information.',
  },
  {
    title: 'Kubernetes',
    sample: 'kubectl get events --sort-by=.lastTimestamp',
    command: 'kubectl',
    covers: 'Pod and deployment state, events, and rollout history.',
  },
  {
    title: 'Redis',
    sample: 'redis-cli SLOWLOG GET 10',
    command: 'redis-cli',
    covers: 'INFO, SLOWLOG, LATENCY, DBSIZE, and SCAN diagnostics.',
  },
  {
    title: 'MongoDB',
    sample: 'mongosh --eval "db.serverStatus().connections"',
    command: 'mongosh',
    covers: 'MongoDB diagnostics through a read-only database role.',
  },
  {
    title: 'PostgreSQL',
    sample: 'psql -c "SELECT * FROM pg_stat_activity;"',
    command: 'psql',
    covers: 'PostgreSQL diagnostics through a read-only database role.',
  },
  {
    title: 'MySQL',
    sample: 'mysql -e "SHOW FULL PROCESSLIST;"',
    command: 'mysql',
    covers: 'MySQL diagnostics through a read-only role and option file.',
  },
  {
    title: 'Elasticsearch',
    sample: 'es cluster health -o json',
    command: 'es',
    covers: 'Optional ELK cluster health, index state, Query DSL, and SQL search.',
  },
] as const;

export function ToolbeltSlider() {
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const tweenRef = useRef<ReturnType<typeof gsap.to> | null>(null);
  const activeRef = useRef(0);
  const reducedMotionRef = useRef(false);
  const dragRef = useRef({
    active: false,
    pointerId: -1,
    startX: 0,
    startIndex: 0,
  });

  useEffect(() => {
    const root = rootRef.current;
    const list = listRef.current;
    if (!root || !list) return;

    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    reducedMotionRef.current = media.matches;
    const context = gsap.context(() => {
      gsap.set(list, { '--product-slide-index': activeRef.current });
    }, root);

    const handlePreferenceChange = (event: MediaQueryListEvent) => {
      reducedMotionRef.current = event.matches;
      if (event.matches) {
        dragRef.current.active = false;
        const collection = root.querySelector<HTMLElement>(
          '.toolbelt-slider__collection',
        );
        if (collection) collection.dataset.dragging = 'false';
        tweenRef.current?.kill();
        gsap.set(list, { '--product-slide-index': activeRef.current });
      }
    };

    media.addEventListener('change', handlePreferenceChange);
    return () => {
      media.removeEventListener('change', handlePreferenceChange);
      tweenRef.current?.kill();
      context.revert();
    };
  }, []);

  const selectTool = (index: number) => {
    const list = listRef.current;
    const next = (index + tools.length) % tools.length;
    activeRef.current = next;
    setActive(next);
    if (!list) return;

    tweenRef.current?.kill();
    if (reducedMotionRef.current) {
      gsap.set(list, { '--product-slide-index': next });
      return;
    }

    tweenRef.current = gsap.to(list, {
      '--product-slide-index': next,
      duration: 1.5,
      ease: 'expo.out',
      overwrite: true,
    });
  };

  const move = (direction: number) => selectTool(activeRef.current + direction);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      move(-1);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      move(1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      selectTool(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      selectTool(tools.length - 1);
    }
  };

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (reducedMotionRef.current || event.button !== 0) return;
    tweenRef.current?.kill();
    dragRef.current = {
      active: true,
      pointerId: event.pointerId,
      startX: event.clientX,
      startIndex: activeRef.current,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.dataset.dragging = 'true';
  };

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    const list = listRef.current;
    if (!drag.active || drag.pointerId !== event.pointerId || !list) return;

    const rootFontSize =
      Number.parseFloat(getComputedStyle(document.body).fontSize) || 16;
    const slideWidth = 23.75 * rootFontSize;
    const progress =
      drag.startIndex - (event.clientX - drag.startX) / slideWidth;
    gsap.set(list, { '--product-slide-index': progress });
  };

  const handlePointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag.active || drag.pointerId !== event.pointerId) return;

    dragRef.current.active = false;
    event.currentTarget.dataset.dragging = 'false';
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    const rootFontSize =
      Number.parseFloat(getComputedStyle(document.body).fontSize) || 16;
    const slideWidth = 23.75 * rootFontSize;
    const delta = event.clientX - drag.startX;
    const target =
      Math.abs(delta) >= slideWidth * 0.1
        ? drag.startIndex + (delta < 0 ? 1 : -1)
        : drag.startIndex;
    selectTool(target);
  };

  return (
    <section
      ref={rootRef}
      className="osmo-section toolbelt-slider"
      aria-labelledby="toolbelt-heading"
    >
      <div className="osmo-container toolbelt-slider__heading">
        <div>
          <h2 id="toolbelt-heading" className="toolbelt-slider__title">
            One toolbelt, twelve CLIs
          </h2>
        </div>
        <p className="reckon-scribble toolbelt-slider__scribble">
          twelve CLIs, one session
        </p>
      </div>

      <div
        className="toolbelt-slider__carousel"
        role="group"
        tabIndex={0}
        aria-roledescription="carousel"
        aria-label="Read-only CLI toolbelt"
        onKeyDown={handleKeyDown}
      >
        <div
          className="toolbelt-slider__tabs"
          role="tablist"
          aria-label="CLI tools"
        >
          {tools.map((tool, index) => (
            <button
              className="toolbelt-slider__tab"
              type="button"
              role="tab"
              aria-selected={index === active}
              aria-controls={`toolbelt-slide-${index}`}
              onClick={() => selectTool(index)}
              key={tool.title}
            >
              {tool.title}
            </button>
          ))}
        </div>

        <div
          className="toolbelt-slider__collection"
          data-dragging="false"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerEnd}
          onPointerCancel={handlePointerEnd}
        >
          <div
            ref={listRef}
            className="toolbelt-slider__list"
            style={{ '--product-slide-index': 0 } as CSSProperties}
          >
            {tools.map((tool, index) => (
              <article
                id={`toolbelt-slide-${index}`}
                className="toolbelt-card"
                aria-roledescription="slide"
                aria-label={`${index + 1} of ${tools.length}: ${tool.title}`}
                aria-hidden={index !== active}
                data-active={index === active ? 'true' : 'false'}
                key={tool.title}
              >
                <div className="toolbelt-card__content">
                  <div className="toolbelt-card__top">
                    <span className="toolbelt-card__command">{tool.command}</span>
                    <span className="toolbelt-card__tag">read only</span>
                  </div>
                  <div className="toolbelt-card__center">
                    <span className="toolbelt-card__prompt" aria-hidden="true">
                      &gt;_
                    </span>
                    <h3>{tool.title}</h3>
                    <p>{tool.covers}</p>
                  </div>
                  <div className="toolbelt-card__footer">
                    <code className="toolbelt-card__sample">
                      <span aria-hidden>$ </span>
                      {tool.sample}
                    </code>
                    <span className="toolbelt-card__count">
                      {String(index + 1).padStart(2, '0')} / {tools.length}
                    </span>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
