"use client";

import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";

import { EmptyState } from "@/components/empty-state";
import { LoadingBlock } from "@/components/loading-block";

interface QueryBoundaryProps<T> {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  /** Spinner label rendered while the query is loading. */
  loadingLabel?: string;
  /** Title for the error/empty-data empty state. Detail defaults to `query.error?.message`. */
  errorTitle?: string;
  /** When provided AND `isEmpty` returns true for the loaded data, render `<EmptyState>` with this title. */
  emptyTitle?: string;
  /** Predicate that decides whether a successful response should be treated as empty. */
  isEmpty?: (data: T) => boolean;
}

/**
 * Render-prop boundary that collapses the async query-state ladder
 * (loading → error → empty → success) into a single call site.
 *
 * The default loading card is rendered via the shared `<LoadingBlock>` component.
 */
export function QueryBoundary<T>({
  query,
  children,
  loadingLabel = "Loading",
  errorTitle = "Something went wrong",
  emptyTitle,
  isEmpty = () => false,
}: QueryBoundaryProps<T>) {
  if (query.isLoading) {
    return <LoadingBlock label={loadingLabel} />;
  }

  if (query.isError || !query.data) {
    return <EmptyState title={errorTitle} detail={query.error?.message} />;
  }

  const data = query.data;
  if (emptyTitle !== undefined && isEmpty(data)) {
    return <EmptyState title={emptyTitle} />;
  }

  return <>{children(data)}</>;
}
