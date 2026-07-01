import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE_DIR = path.resolve(__dirname, "../.cache/photos");

const sourceUrl = (id: number) => `https://cdn.nba.com/headshots/nba/latest/260x190/${id}.png`;

// NBA's CDN returns HTTP 200 with a generic ~5KB silhouette for any
// player_id without a real headshot, rather than 404ing — verified: a real
// photo at this size is consistently ~17KB (checked several players from
// the 1940s through today), while an unmapped id returns ~5KB. 10KB splits
// the two clusters with margin on both sides.
const MIN_REAL_PHOTO_BYTES = 10_000;

const pngPath = (id: number) => path.join(CACHE_DIR, `${id}.png`);
const nonePath = (id: number) => path.join(CACHE_DIR, `${id}.none`);

async function readIfExists(file: string): Promise<Buffer | null> {
  try {
    return await fs.readFile(file);
  } catch {
    return null;
  }
}

/** Returns the cached/fetched headshot bytes, or null if this player has no
 *  real photo (NBA serves a generic silhouette instead). Caches both
 *  outcomes to disk so repeat views don't re-hit the NBA CDN. */
export async function getPlayerPhoto(id: number): Promise<Buffer | null> {
  await fs.mkdir(CACHE_DIR, { recursive: true });

  const cached = await readIfExists(pngPath(id));
  if (cached) return cached;
  if (await readIfExists(nonePath(id))) return null;

  const res = await fetch(sourceUrl(id));
  if (!res.ok) {
    await fs.writeFile(nonePath(id), "");
    return null;
  }
  const buffer = Buffer.from(await res.arrayBuffer());
  if (buffer.byteLength < MIN_REAL_PHOTO_BYTES) {
    await fs.writeFile(nonePath(id), "");
    return null;
  }
  await fs.writeFile(pngPath(id), buffer);
  return buffer;
}
