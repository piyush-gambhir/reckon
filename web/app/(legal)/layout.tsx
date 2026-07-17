import { FloatingHeader } from '@/components/floating-header';
import { SiteFooter } from '@/components/site-footer';

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <div className="marketing-shell legal-shell flex min-h-screen flex-col">
      <FloatingHeader />
      {children}
      <SiteFooter />
    </div>
  );
}
