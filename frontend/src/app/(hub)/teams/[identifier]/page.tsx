import { Suspense } from "react";

import { TeamHub } from "@/features/team-hub/components/team-hub";

interface TeamPageProps {
  params: Promise<{ identifier: string }>;
}

export default async function TeamPage({ params }: TeamPageProps) {
  const { identifier } = await params;
  return (
    <Suspense fallback={<div className="min-h-screen bg-court-paper p-6 text-sm text-court-muted">Loading team</div>}>
      <TeamHub identifier={identifier} />
    </Suspense>
  );
}
