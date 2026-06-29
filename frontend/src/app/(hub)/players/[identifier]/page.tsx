import { Suspense } from "react";

import { PlayerHub } from "@/features/player-hub/components/player-hub";

interface PlayerPageProps {
  params: Promise<{ identifier: string }>;
}

export default async function PlayerPage({ params }: PlayerPageProps) {
  const { identifier } = await params;
  return (
    <Suspense fallback={<div className="min-h-screen bg-court-paper p-6 text-sm text-court-muted">Loading player</div>}>
      <PlayerHub identifier={identifier} />
    </Suspense>
  );
}
