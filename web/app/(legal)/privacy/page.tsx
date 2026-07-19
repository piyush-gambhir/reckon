import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage } from '@/components/legal-page';
import { createPageMetadata, describePage } from '@/lib/metadata';

export const metadata: Metadata = createPageMetadata({
  title: 'Privacy Policy',
  description: describePage('Privacy Policy covering local credentials, external connections, and investigation data in reckon.'),
  path: '/privacy',
});

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      intro={
        <>
          reckon is an independent, personal open-source project that runs in a
          workspace on your own computer. It does <strong>not</strong> collect,
          transmit, or store personal data on any server operated by the
          maintainer.
        </>
      }
    >
      <section>
        <h2>1. No maintainer data collection</h2>
        <p>
          The maintainer operates <strong>no reckon backend service</strong> and
          receives <strong>no investigation data</strong> from your use of the
          workspace. The project includes no analytics, telemetry, tracking, or
          advertising.
        </p>
      </section>

      <section>
        <h2>2. Credentials and local storage</h2>
        <p>
          Credentials and tool profiles are stored on your device. The setup
          isolates supported configuration beneath the cloned workspace and
          loads environment values from local files such as <code>.env</code>.
          You are responsible for protecting those files, using read-only
          credentials, and keeping them out of version control.
        </p>
      </section>

      <section>
        <h2>3. External connections</h2>
        <p>
          The tools wired into reckon connect directly from your computer to
          the services you configure, including observability, CI/CD,
          infrastructure, source-control, queue, cache, and database systems.
          Those connections are between you and the relevant provider. The
          maintainer cannot observe them.
        </p>
      </section>

      <section>
        <h2>4. Investigation data</h2>
        <p>
          Command output, evidence, and RCA reports remain in your terminal or
          in files you create locally, including the workspace&apos;s{' '}
          <code>incidents/</code> directory. reckon does not upload that content
          to the maintainer.
        </p>
      </section>

      <section>
        <h2>5. Third-party tools</h2>
        <p>
          reckon installs or invokes independent command-line tools. Each tool
          and connected service has its own privacy terms and data-handling
          behavior. Review those terms before connecting production systems.
        </p>
      </section>

      <section>
        <h2>6. Changes to this policy</h2>
        <p>Changes will be posted on this page with an updated effective date.</p>
      </section>

      <section>
        <h2>7. Contact</h2>
        <p>
          Questions about this policy can be sent to{' '}
          <a href="mailto:developer.piyushgambhir@gmail.com">
            developer.piyushgambhir@gmail.com
          </a>
          , or through the <Link href="/contact">contact page</Link>.
        </p>
      </section>
    </LegalPage>
  );
}
