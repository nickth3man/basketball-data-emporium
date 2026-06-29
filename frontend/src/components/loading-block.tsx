import { LoaderCircle } from "lucide-react";

import { cn } from "@/lib/cn";

interface LoadingBlockProps {
  /** Label shown next to the spinner. Defaults to "Loading". */
  label?: string;
  /** Tailwind height utility for the centered area, e.g. "h-40" or "h-96". Defaults to "h-40". */
  minHeight?: string;
}

/** Centered card-style loading spinner — the shared loading affordance for content areas. */
export function LoadingBlock({ label = "Loading", minHeight = "h-40" }: LoadingBlockProps) {
  return (
    <div
      className={cn(
        "flex items-center justify-center rounded-md border border-court-line bg-white text-sm text-court-muted",
        minHeight,
      )}
    >
      <LoaderCircle className="mr-2 size-4 animate-spin" aria-hidden="true" />
      {label}
    </div>
  );
}
