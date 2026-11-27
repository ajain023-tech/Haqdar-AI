// Shareable result links carry only eligibility fields (never names, phone numbers or IDs),
// base64url-encoded in the URL: /result?p=<profile>.
import type { Profile } from "./types";

const FIELDS: (keyof Profile)[] = ["state", "age", "gender", "occupation", "annual_income", "land_acres",
  "social_category", "disability", "bpl"];

function clean(p: Record<string, unknown>): Profile {
  const out: Record<string, unknown> = {};
  for (const f of FIELDS) if (p[f] !== undefined && p[f] !== null && p[f] !== "") out[f] = p[f];
  return out as Profile;
}

export function encodeProfile(p: Profile): string {
  const json = JSON.stringify(clean(p as Record<string, unknown>));
  const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(json)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodeProfile(s: string | null): Profile | null {
  if (!s) return null;
  try {
    const b64 = s.replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    return clean(JSON.parse(new TextDecoder().decode(bytes)));
  } catch {
    return null;
  }
}
