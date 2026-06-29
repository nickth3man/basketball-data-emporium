"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

/** Read/write a single key in the current URL's search params via `router.replace`. */
export function useUrlParam() {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  function get(key: string): string | null {
    return searchParams.get(key);
  }

  function set(key: string, value: string): void {
    const params = new URLSearchParams(searchParams.toString());
    params.set(key, value);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }

  return { get, set };
}
