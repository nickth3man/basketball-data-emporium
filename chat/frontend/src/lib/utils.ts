/**
 * Tiny className combiner. Wraps clsx so call-sites get clean conditional
 * merging (`cn("base", cond && "extra", { "on": flag })`) without every
 * component pulling clsx directly.
 */
import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
