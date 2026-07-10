import "@testing-library/jest-dom/vitest";
import { afterEach, afterAll, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { handlers } from "./mocks/handlers";

// MSW request-mocking server (Node). Enabled for every test by default;
// unhandled requests error loudly so missing mocks are caught immediately.
// To opt a specific test out, override with `server.use(...)` or
// `server.restoreHandlers()` within that test.
export const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

// React 19 act() environment flag.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
