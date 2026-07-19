import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage } from '@/components/legal-page';
import { createPageMetadata, describePage } from '@/lib/metadata';

export const metadata: Metadata = createPageMetadata({
  title: 'Contact',
  description: describePage('Contact details, support channels, and security reporting guidance for reckon.'),
  path: '/contact',
});

export default function ContactPage() {
  return (
    <LegalPage
      title="Contact"
      intro={
        <>
          reckon is a personal open-source project maintained by{' '}
          <strong>Piyush Gambhir</strong>. Support is best-effort. These are the
          best ways to get in touch.
        </>
      }
    >
      <div className="legal-contact-grid">
        <article className="legal-contact-card">
          <h2>Email</h2>
          <p className="legal-contact-card__link">
            <a href="mailto:developer.piyushgambhir@gmail.com">
              developer.piyushgambhir@gmail.com
            </a>
          </p>
          <p>General questions, privacy requests, and private security reports.</p>
        </article>
        <article className="legal-contact-card">
          <h2>Bugs and features</h2>
          <p className="legal-contact-card__link">
            <a
              href="https://github.com/piyush-gambhir/reckon/issues"
              target="_blank"
              rel="noreferrer"
            >
              GitHub Issues ↗
            </a>
          </p>
          <p>Report a reproducible bug or propose a project improvement.</p>
        </article>
        <article className="legal-contact-card">
          <h2>Source</h2>
          <p className="legal-contact-card__link">
            <a
              href="https://github.com/piyush-gambhir/reckon"
              target="_blank"
              rel="noreferrer"
            >
              piyush-gambhir/reckon ↗
            </a>
          </p>
          <p>Read the code, review the documentation, or contribute a change.</p>
        </article>
      </div>

      <section>
        <h2>Security issues</h2>
        <p>
          If you find a vulnerability that could expose credentials or
          production data, email{' '}
          <a href="mailto:developer.piyushgambhir@gmail.com">
            developer.piyushgambhir@gmail.com
          </a>{' '}
          instead of opening a public issue. Do not include live credentials or
          sensitive incident evidence.
        </p>
      </section>

      <section>
        <h2>Response time</h2>
        <p>
          This is an independent personal project, not a commercial product.
          The maintainer responds when possible, but no response time or support
          level is guaranteed. See the <Link href="/terms">Terms of Service</Link>{' '}
          for the warranty and liability terms.
        </p>
      </section>

      <section>
        <h2>Vendor support</h2>
        <p>
          reckon is not affiliated with the vendors of its connected tools. For
          problems with a vendor service or account, use that provider&apos;s
          official support channel.
        </p>
      </section>
    </LegalPage>
  );
}
