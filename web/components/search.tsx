'use client';
import {
  SearchDialog,
  SearchDialogClose,
  SearchDialogContent,
  SearchDialogHeader,
  SearchDialogIcon,
  SearchDialogInput,
  SearchDialogList,
  SearchDialogOverlay,
  type SharedProps,
} from 'fumadocs-ui/components/dialog/search';
import { useDocsSearch } from 'fumadocs-core/search/client';
import { staticClient } from 'fumadocs-core/search/client/orama-static';
import { useI18n } from 'fumadocs-ui/contexts/i18n';

export default function DefaultSearchDialog(props: SharedProps) {
  const { locale } = useI18n(); // (optional) for i18n
  const { search, setSearch, query } = useDocsSearch({
    client: staticClient({
      locale,
    }),
  });

  return (
    <SearchDialog search={search} onSearchChange={setSearch} isLoading={query.isLoading} {...props}>
      <SearchDialogOverlay className="reckon-search-overlay" />
      <SearchDialogContent className="reckon-search-dialog" data-lenis-prevent="">
        <SearchDialogHeader className="reckon-search-header">
          <SearchDialogIcon className="reckon-search-icon" />
          <SearchDialogInput className="reckon-search-input" />
          <SearchDialogClose className="reckon-search-close" />
        </SearchDialogHeader>
        <SearchDialogList
          className="reckon-search-results"
          items={query.data !== 'empty' ? query.data : null}
        />
      </SearchDialogContent>
    </SearchDialog>
  );
}
