// Headless screenshot helper for the new "Best career value" section.
// Uses playwright-core (no save flag) driving Playwright's bundled chromium.
// Saves web/screenshot-draft-value.png and exits non-zero on error.
import { chromium } from "playwright-core";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CHROME = "C:/Users/nicolas/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe";
const URL = process.env.SHOT_URL ?? "http://localhost:5173/";
const OUT = path.resolve(__dirname, "../screenshot-draft-value.png");

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const ctx = await browser.newContext({ viewport: { width: 1800, height: 2400 } });
  const page = await ctx.newPage();

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
  page.on("requestfailed", (req) =>
    consoleErrors.push(`requestfailed: ${req.url()} (${req.failure()?.errorText})`),
  );
  page.on("response", (res) => {
    if (res.status() >= 400) consoleErrors.push(`HTTP ${res.status()}: ${res.url()}`);
  });

  await page.goto(URL, { waitUntil: "networkidle" });

  // Click the "Draft & Awards" tab.
  await page.click('[data-tab="draft-awards"]');

  // Wait for the new section to render its table.
  const section = page.locator("section", { hasText: "Best career value" });
  await section.waitFor({ timeout: 10000 });
  await section.locator("h2").waitFor();
  // Default load should populate 50 rows.
  await page.waitForFunction(
    () => {
      const secs = Array.from(document.querySelectorAll("section.subsection"));
      const career = secs.find((s) => /Best career value/.test(s.textContent ?? ""));
      return Boolean(career && career.querySelector("tbody tr"));
    },
    null,
    { timeout: 10000 },
  );

  // Read the table HTML to a debug log and write the screenshot.
  const tableHtml = await section.evaluate((s) => s.outerHTML);
  writeFileSync(
    path.resolve(__dirname, "../screenshot-draft-value.html"),
    `<!doctype html><meta charset="utf-8"><title>draft value section</title>${tableHtml}`,
  );

  // Screenshot the full section, including its heading, controls, and table.
  // Force the table-scroll to render all 12 columns without horizontal clipping
  // by widening the section temporarily for the screenshot capture only.
  await page.evaluate((sel) => {
    const section = Array.from(document.querySelectorAll("section.subsection")).find((s) =>
      /Best career value/.test(s.textContent ?? ""),
    );
    if (!section) return;
    const wrapper = section.querySelector(".table-scroll");
    if (wrapper) {
      wrapper.style.overflowX = "visible";
      wrapper.style.maxWidth = "none";
    }
    section.style.maxWidth = "none";
    section.style.width = "fit-content";
  }, "career-value-sort");
  await page.waitForTimeout(100);
  const sectionBox = await section.boundingBox();
  console.log(`Section box: ${JSON.stringify(sectionBox)}`);
  await section.screenshot({ path: OUT });
  console.log(`Saved screenshot to ${OUT}`);

  // Functional checks (logged so the orchestrator can see what passed).
  const visiblePlayers = await section
    .locator("table tbody tr td button.cell-link")
    .allTextContents();
  console.log(`First 5 visible players: ${visiblePlayers.slice(0, 5).join(", ")}`);

  // Round=1 should still yield rows and have ≤ the all-rounds count.
  await page.selectOption("#career-value-round", "1");
  await page.waitForFunction(
    () => {
      const secs = Array.from(document.querySelectorAll("section.subsection"));
      const career = secs.find((s) => /Best career value/.test(s.textContent ?? ""));
      return Boolean(career && career.querySelector("tbody tr"));
    },
    null,
    { timeout: 10000 },
  );
  const round1Rows = await section.locator("table tbody tr").count();
  console.log(`Round=1 row count: ${round1Rows}`);

  // Sort by FG% — percentage column should display with % suffix.
  await page.selectOption("#career-value-round", "");
  await page.selectOption("#career-value-sort", "career_fg_pct");
  await page.waitForFunction(
    () => {
      const secs = Array.from(document.querySelectorAll("section.subsection"));
      const career = secs.find((s) => /Best career value/.test(s.textContent ?? ""));
      const headers = career
        ? Array.from(career.querySelectorAll("thead th")).map((t) => t.textContent)
        : [];
      return headers.includes("FG%");
    },
    null,
    { timeout: 10000 },
  );
  const fgValues = await section.locator("table tbody tr td:nth-child(11)").allTextContents();
  console.log(`Top 5 FG% values: ${fgValues.slice(0, 5).join(", ")}`);

  if (consoleErrors.length) {
    console.error("Console errors detected:");
    for (const e of consoleErrors) console.error("  " + e);
    await browser.close();
    process.exitCode = 1;
    return;
  }
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
