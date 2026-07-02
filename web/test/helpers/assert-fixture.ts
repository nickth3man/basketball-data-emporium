// Assertion driver for `DataFixture`s. Splits the work into two halves so
// the test file can register `test.fails` for known regressions:
//
//   - `executeAssertion(fixture)` runs the query (query_fn / raw_sql /
//     composite), applies the extract path, and returns the *actual*
//     value. It does not assert anything — test.fails needs to see the
//     real Vitest assertion failure to mark a test as "expected to fail".
//
//   - `applyMatch(actual, fixture)` performs the Vitest match for the
//     declared `fixture.match` mode.
import { expect } from "vitest";
import { queryObjects } from "../../server/db";
import type { DuckDBValue } from "@duckdb/node-api";
import * as q from "../../server/queries";
import type { DataFixture } from "../fixtures/manifest";

/** Walks a dot/bracket path into a value. Returns `value` unchanged when
 *  `path` is empty/missing. Returns `undefined` (no throw) when a
 *  segment is missing on the way down — fixtures that index into
 *  unpopulated data should still be runnable. */
export function extractByPath(value: unknown, path?: string): unknown {
  if (!path) return value;

  // Tokenise: split on '.' and '[' ... ']'. Examples:
  //   "career.gp"           -> ["career", "gp"]
  //   "jerseyHistory[0].jersey_num" -> ["jerseyHistory", "0", "jersey_num"]
  const tokens: string[] = [];
  let i = 0;
  while (i < path.length) {
    const c = path[i];
    if (c === ".") {
      i++;
      continue;
    }
    if (c === "[") {
      const end = path.indexOf("]", i);
      if (end === -1) break;
      tokens.push(path.slice(i + 1, end));
      i = end + 1;
      continue;
    }
    let j = i;
    while (j < path.length && path[j] !== "." && path[j] !== "[") j++;
    tokens.push(path.slice(i, j));
    i = j;
  }

  let cur: unknown = value;
  for (const tok of tokens) {
    if (cur === null || cur === undefined) return undefined;
    cur = (cur as Record<string, unknown>)[tok];
  }
  return cur;
}

export async function executeAssertion(fixture: DataFixture): Promise<unknown> {
  const target = fixture.query_target;
  switch (fixture.assertion_mode) {
    case "query_fn": {
      if (typeof target.fn !== "string") {
        throw new Error(
          `fixture ${fixture.id}: query_target.fn is required for assertion_mode=query_fn`,
        );
      }
      const fnName = target.fn;
      const fn = (q as unknown as Record<string, (...a: unknown[]) => Promise<unknown>>)[fnName];
      if (typeof fn !== "function") {
        throw new Error(
          `fixture ${fixture.id}: query function '${fnName}' not exported from web/server/queries.ts`,
        );
      }
      const result = await fn(...(target.params ?? []));
      return extractByPath(result, target.extract);
    }
    case "raw_sql": {
      if (typeof target.sql !== "string") {
        throw new Error(
          `fixture ${fixture.id}: query_target.sql is required for assertion_mode=raw_sql`,
        );
      }
      const rows = await queryObjects(target.sql, target.params as DuckDBValue[] | undefined);
      return extractByPath(rows, target.extract);
    }
    case "composite": {
      if (!Array.isArray(target.composite)) {
        throw new Error(
          `fixture ${fixture.id}: query_target.composite must be an array for assertion_mode=composite`,
        );
      }
      const results: unknown[] = [];
      for (const sub of target.composite) {
        const transient: DataFixture = {
          ...fixture,
          assertion_mode: sub.mode,
          query_target: {
            fn: sub.fn,
            params: sub.params,
            sql: sub.sql,
            extract: sub.extract,
          },
        };
        results.push(await executeAssertion(transient));
      }
      return results;
    }
    default:
      throw new Error(
        `fixture ${fixture.id}: unknown assertion_mode '${String(fixture.assertion_mode)}'`,
      );
  }
}

export function applyMatch(actual: unknown, fixture: DataFixture): void {
  switch (fixture.match) {
    case "equals":
      expect(actual).toEqual(fixture.expected);
      return;
    case "closeTo":
      expect(actual).toBeCloseTo(Number(fixture.expected), fixture.tolerance ?? 2);
      return;
    case "containsObject":
      expect(actual as unknown[]).toEqual(
        expect.arrayContaining([expect.objectContaining(fixture.expected as object)]),
      );
      return;
    case "notContainsObject":
      expect(
        (actual as unknown[]).find((el) => {
          try {
            expect(el).toEqual(expect.objectContaining(fixture.expected as object));
            return true;
          } catch {
            return false;
          }
        }),
      ).toBeUndefined();
      return;
    case "arrayContains":
      expect(actual).toEqual(expect.arrayContaining(fixture.expected as unknown[]));
      return;
    case "objectMatching":
      expect(actual).toMatchObject(fixture.expected as object);
      return;
    case "length":
      expect(actual as unknown[]).toHaveLength(Number(fixture.expected));
      return;
    case "gte":
      expect(actual as number).toBeGreaterThanOrEqual(Number(fixture.expected));
      return;
    default:
      throw new Error(`fixture ${fixture.id}: unknown match mode '${String(fixture.match)}'`);
  }
}
