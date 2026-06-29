import { Suspense } from "react";

import { LoadingBlock } from "@/components/loading-block";
import { SeasonHub } from "@/features/season-hub/components/season-hub";

export default function SeasonsPage() {
  return (
    <Suspense fallback={<LoadingBlock label="Loading seasons" />}>
      <SeasonHub />
    </Suspense>
  );
}
