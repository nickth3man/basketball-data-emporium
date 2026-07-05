/**
 * Phase 5 entry point — the chat shell (PLAN §15 Phase 5 exit criteria).
 *
 * Phase 2's typed-wiring demo was replaced wholesale by `<ChatView />`;
 * the only reason to keep the file thin is so `main.tsx` can keep
 * importing `<App />` without churn.
 */
import { ChatView } from "@/views/ChatView";

export function App() {
  return <ChatView />;
}
