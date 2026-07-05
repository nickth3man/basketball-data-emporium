import { useEffect, useState } from "react";

type HealthState = "loading" | { db: string } | { error: string };

export function App() {
  const [health, setHealth] = useState<HealthState>("loading");

  useEffect(() => {
    let cancelled = false;

    const checkHealth = async (): Promise<void> => {
      try {
        const res = await fetch("/api/health");
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data: unknown = await res.json();
        if (cancelled) {
          return;
        }
        if (
          data !== null &&
          typeof data === "object" &&
          "db" in data &&
          typeof (data as { db: unknown }).db === "string"
        ) {
          setHealth({ db: (data as { db: string }).db });
        } else {
          setHealth({ error: "unexpected health response shape" });
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        const message = err instanceof Error ? err.message : "unknown error";
        setHealth({ error: message });
      }
    };

    void checkHealth();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col items-center gap-6 px-6 py-12">
      <header className="flex flex-col items-center gap-2 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Basketball Data Chatbot</h1>
        <p className="text-sm text-[color:var(--color-muted-foreground)]">
          Phase 0 scaffolding — chat shell arrives in Phase 5.
        </p>
      </header>

      <main className="flex w-full flex-1 flex-col items-center justify-start gap-4">
        <p className="text-sm">Warehouse status: {renderHealth(health)}</p>

        {/* ChatView will be mounted here in Phase 5 (PLAN §8.3). */}
        <div className="hidden" aria-hidden="true" />
      </main>
    </div>
  );
}

function renderHealth(health: HealthState): string {
  if (health === "loading") {
    return "checking…";
  }
  if ("error" in health) {
    return `health check failed (${health.error})`;
  }
  return health.db;
}
