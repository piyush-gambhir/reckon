import type { ReactNode } from 'react';

export function LegalPage({
  title,
  intro,
  children,
}: {
  title: string;
  intro: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="legal-page">
      <div className="legal-page__inner">
        <header className="legal-page__header">
          <h1>{title}</h1>
          <p className="legal-page__effective">Effective July 17, 2026</p>
        </header>
        <p className="legal-page__lede">{intro}</p>
        <div className="legal-page__content">{children}</div>
      </div>
    </main>
  );
}
