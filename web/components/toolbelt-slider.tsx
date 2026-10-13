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
    title: 'Jenkins',
    sample: 'reckon run jenkins -- build list deploy-api --limit 20',
    command: 'jenkins',
    covers: 'Jobs, builds, pipelines and logs. One connection per server, so prod and UAT stay apart.',
  },
  {
    title: 'CubeAPM',
    sample: 'reckon run cubeapm -- traces search --service checkout --status error',
    command: 'cubeapm',
    covers: 'Traces, PromQL metrics and LogsQL logs. A shared server stays on the active environment\'s label.',
  },
  {
    title: 'Grafana',
    sample: 'reckon run grafana -- alert rule list -o json',
    command: 'grafana',
    covers: 'Dashboards, datasources, alert rules and annotations.',
  },
  {
    title: 'GitHub',
    sample: 'reckon run github -- pr list --repo acme/api --state merged',
    command: 'gh',
    covers: 'Pull requests, Actions runs, releases and issues.',
  },
  {
    title: 'Jira',
    sample: 'reckon run jira -- issue search --jql \'project = APP\' --limit 50',
    command: 'jira',
    covers: 'Issues, sprints, boards and history for product work.',
  },
  {
    title: 'Search Console',
    sample: 'reckon run gsc -- top queries --site sc-domain:acme.com --last 28d',
    command: 'gsc',
    covers: 'Search performance, opportunities, sitemaps and URL inspection.',
  },
  {
    title: 'Bing Webmaster',
    sample: 'reckon run bing -- stats summary --days 28 --compare previous',
    command: 'bwt',
    covers: 'Bing traffic, queries, crawl issues and sitemaps.',
  },
  {
    title: 'Clarity',
    sample: 'reckon run clarity -- recordings list --days 2 --rage-clicks',
    command: 'clarity',
    covers: 'Session recordings and behaviour metrics from Microsoft Clarity.',
  },
  {
    title: 'AWS',
    sample: 'reckon run aws -- logs filter-log-events --log-group-name /ecs/checkout',
    command: 'aws',
    covers: 'CloudWatch, ALB, ECS, SQS and RDS reads. Only listed read commands run.',
  },
  {
    title: 'Kubernetes',
    sample: 'reckon run kubernetes -- get events --sort-by=.lastTimestamp',
    command: 'kubectl',
    covers: 'Pod and deployment state, events and rollout history. Secrets are refused.',
  },
  {
    title: 'Nginx Proxy Manager',
    sample: 'reckon run nginxpm -- proxy list -o json',
    command: 'nginxpm',
    covers: 'Proxy hosts, certificates, redirects and access lists.',
  },
  {
    title: 'Elasticsearch',
    sample: 'reckon run elasticsearch -- cluster health -o json',
    command: 'es',
    covers: 'Cluster health, index state, Query DSL and SQL search.',
  },
  {
    title: 'Kafka',
    sample: 'reckon run kafka-rpk -- group describe checkout-consumers',
    command: 'kcat + rpk',
    covers: 'Metadata, group-less topic reads and consumer-group lag. Each run asks you first.',
  },
  {
    title: 'Redis',
    sample: 'reckon run redis -- SLOWLOG GET 10',
    command: 'redis-cli',
    covers: 'INFO, SLOWLOG, LATENCY, DBSIZE and SCAN diagnostics. Each run asks you first.',
  },
  {
    title: 'PostgreSQL',
    sample: 'reckon run postgres -- -c "SELECT * FROM pg_stat_activity LIMIT 20"',
    command: 'psql',
    covers: 'Diagnostics through a read-only role; Reckon starts every session read-only.',
  },
  {
    title: 'MongoDB',
    sample: 'reckon run mongodb -- --eval "db.serverStatus().connections"',
    command: 'mongosh',
    covers: 'MongoDB diagnostics through a read-only database role.',
  },
  {
    title: 'MySQL',
    sample: 'reckon run mysql -- -e "SHOW FULL PROCESSLIST"',
    command: 'mysql',
    covers: 'MySQL diagnostics through a read-only role; sessions start read-only.',
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
            One toolbelt, every connection
          </h2>
        </div>
        <p className="reckon-scribble toolbelt-slider__scribble">
          one command, the right account
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
