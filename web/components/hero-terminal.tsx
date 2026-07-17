'use client';

import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

type Phase = 'static' | 'playing' | 'done';

function isComment(line: string) {
  return line.trimStart().startsWith('#');
}

function isBlank(line: string) {
  return line.trim() === '';
}

function Line({ line }: { line: string }) {
  if (isBlank(line)) return <span>{'\n'}</span>;

  if (isComment(line)) {
    return <span className="hero-terminal__comment">{line}</span>;
  }

  const tokens = line.split(/(\s+)/);
  let seenBinary = false;

  return (
    <span>
      {tokens.map((tok, i) => {
        if (/^\s+$/.test(tok)) return <span key={i}>{tok}</span>;

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
        return <span key={i}>{tok}</span>;
      })}
    </span>
  );
}

function Prompt() {
  return (
    <span className="hero-terminal__prompt" aria-hidden="true">
      $
    </span>
  );
}

/**
 * A live investigation session: the transcript types itself like a real RCA.
 * Server-rendered (and reduced-motion) output is the complete static
 * transcript; the playback only ever runs client-side on top of it.
 */
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
  const [phase, setPhase] = useState<Phase>('static');
  const [lineIdx, setLineIdx] = useState(0);
  const [charIdx, setCharIdx] = useState(0);

  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const root = rootRef.current;
    if (!root || reduced.matches) return;

    let started = false;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting || started) return;
        started = true;
        observer.disconnect();
        // Small hold so the card has settled after the boot/hero reveal.
        window.setTimeout(() => setPhase('playing'), 600);
      },
      { threshold: 0.35 },
    );
    observer.observe(root);

    const stopPlayback = (event: MediaQueryListEvent) => {
      if (event.matches) setPhase('static');
    };
    reduced.addEventListener('change', stopPlayback);
    return () => {
      observer.disconnect();
      reduced.removeEventListener('change', stopPlayback);
    };
  }, []);

  useEffect(() => {
    if (phase !== 'playing') return;
    if (lineIdx >= lines.length) {
      setPhase('done');
      return;
    }

    const line = lines[lineIdx];
    const advance = () => {
      setLineIdx((i) => i + 1);
      setCharIdx(0);
    };

    let timer: number;
    if (isBlank(line)) {
      timer = window.setTimeout(advance, 140);
    } else if (isComment(line)) {
      // Narration appears as a whole line, like a step label.
      timer = window.setTimeout(advance, 500);
    } else if (charIdx < line.length) {
      timer = window.setTimeout(
        () => setCharIdx((c) => c + 1),
        16 + Math.random() * 22,
      );
    } else {
      // Brief "execute" beat before the next step.
      timer = window.setTimeout(advance, 320);
    }
    return () => window.clearTimeout(timer);
  }, [phase, lineIdx, charIdx, lines]);

  const playing = phase === 'playing';

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
          className={cn('hero-terminal__pulse', playing && 'is--live')}
          aria-hidden="true"
        />
      </div>
      {/* Full transcript for assistive tech regardless of playback state. */}
      <pre className="sr-only">{command}</pre>
      <pre className="hero-terminal__body" aria-hidden="true">
        <code>
          {lines.map((line, i) => {
            const commandLine = !isComment(line) && !isBlank(line);
            let content;
            let hidden = false;

            if (phase === 'static' || i < lineIdx || phase === 'done') {
              content = (
                <>
                  {commandLine ? <Prompt /> : null}
                  <Line line={line} />
                </>
              );
            } else if (i === lineIdx) {
              if (commandLine) {
                content = (
                  <>
                    <Prompt />
                    <span>{line.slice(0, charIdx)}</span>
                    <span className="hero-terminal__caret" />
                  </>
                );
              } else {
                content = <Line line={line} />;
                hidden = isBlank(line);
              }
            } else {
              // Reserve the final layout so nothing shifts while typing.
              content = (
                <>
                  {commandLine ? <Prompt /> : null}
                  <Line line={line} />
                </>
              );
              hidden = true;
            }

            return (
              <span
                className="hero-terminal__line"
                style={hidden ? { visibility: 'hidden' } : undefined}
                key={i}
              >
                {content}
              </span>
            );
          })}
          {phase === 'done' ? (
            <span className="hero-terminal__line hero-terminal__rest">
              <Prompt />
              <span className="hero-terminal__caret is--blinking" />
            </span>
          ) : null}
        </code>
      </pre>
    </div>
  );
}
