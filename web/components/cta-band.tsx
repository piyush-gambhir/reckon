import { ArrowRight } from 'lucide-react';
import { InstallCommand } from '@/components/install-command';
import { OsmoButton } from '@/components/ui/osmo-button';
import { site } from '@/lib/site';

export function CtaBand() {
  return (
    <div className="osmo-cta-card">
      <p className="reckon-scribble osmo-cta-card__scribble">
        we&apos;ll see you on-call
      </p>
      <div className="osmo-cta-card__content">
        <h2 className="osmo-cta-card__title">Ready in one command</h2>
        <p className="osmo-cta-card__body">
          {site.ctaBody ??
            'Install the binary, authenticate, and start querying. No runtime, no dependencies.'}
        </p>
        <InstallCommand
          command={site.installCommand}
          className="osmo-cta-card__install"
        />
        <OsmoButton
          href="/docs"
          className="osmo-cta-card__action"
          icon={<ArrowRight />}
        >
          Read the docs
        </OsmoButton>
      </div>
    </div>
  );
}
