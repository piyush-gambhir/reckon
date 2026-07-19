import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage } from '@/components/legal-page';
import { createPageMetadata, describePage } from '@/lib/metadata';

export const metadata: Metadata = createPageMetadata({
  title: 'Terms of Service',
  description: describePage('Terms covering use, warranty, liability, authorization, and third-party services for reckon.'),
  path: '/terms',
});

export default function TermsPage() {
  return (
    <LegalPage
      title="Terms of Service"
      intro={
        <>
          By cloning, configuring, or using reckon, you agree to these terms.
        </>
      }
    >
      <section>
        <h2>1. Open-source project</h2>
        <p>
          reckon is a personal open-source project. The license and notices
          published in the{' '}
          <a
            href="https://github.com/piyush-gambhir/reckon"
            target="_blank"
            rel="noreferrer"
          >
            repository
          </a>{' '}
          govern your use, copying, modification, and distribution of the
          project.
        </p>
      </section>

      <section>
        <h2>2. No warranty</h2>
        <p>
          The project is provided <strong>&quot;as is&quot;</strong>, without
          warranties of any kind. Production systems are inherently sensitive,
          and you use the workspace and every connected tool at your own risk.
        </p>
      </section>

      <section>
        <h2>3. Authorization and safe use</h2>
        <p>You are responsible for how you use reckon. You must:</p>
        <ul>
          <li>have authorization to access every system you connect;</li>
          <li>use read-only credentials and read-shaped commands;</li>
          <li>follow your organization&apos;s security and incident policies;</li>
          <li>not use the project for unlawful or unauthorized access.</li>
        </ul>
      </section>

      <section>
        <h2>4. Production safeguards</h2>
        <p>
          reckon documents safety conventions, but it cannot guarantee that a
          credential, third-party CLI, agent runtime, or command is read-only.
          You remain responsible for permission boundaries, command review,
          approvals, and the effects of every action.
        </p>
      </section>

      <section>
        <h2>5. Limitation of liability</h2>
        <p>
          To the fullest extent allowed by law, the maintainer is not liable for
          claims, damages, data loss, credential exposure, or service disruption
          arising from use of, or inability to use, reckon.
        </p>
      </section>

      <section>
        <h2>6. Third-party services</h2>
        <p>
          Connected services and command-line tools are governed by their own
          licenses, terms, and policies. The maintainer is not responsible for
          those services or their behavior.
        </p>
      </section>

      <section>
        <h2>7. No affiliation</h2>
        <p>
          reckon is independent and is not affiliated with, endorsed by, or
          sponsored by the vendors of the tools and services it connects. All
          product names and trademarks belong to their respective owners.
        </p>
      </section>

      <section>
        <h2>8. Contact</h2>
        <p>
          Contact <strong>Piyush Gambhir</strong> at{' '}
          <a href="mailto:developer.piyushgambhir@gmail.com">
            developer.piyushgambhir@gmail.com
          </a>
          , or use the <Link href="/contact">contact page</Link>.
        </p>
      </section>
    </LegalPage>
  );
}
