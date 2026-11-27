// Types for the frozen API contract (see README "API contract").

export type Lang = "en" | "hi" | "hinglish";
export type Verdict = "eligible" | "maybe" | "no";
export type Verification = "verified" | "auto" | "stale";

export interface Profile {
  state?: string;
  age?: number;
  gender?: "male" | "female" | "other";
  occupation?: string;
  annual_income?: number;
  land_acres?: number;
  social_category?: "SC" | "ST" | "OBC" | "General";
  disability?: boolean;
  bpl?: boolean;
}

export interface Citation {
  n: number;
  slug: string;
  name: string;
  section: string;
  source_url: string | null;
  last_checked: string | null;
  verification: Verification;
  snippet: string;
}

export interface Answer {
  text: string;
  lang: Lang;
  citations: Citation[];
  refused: boolean;
  disclaimer: string;
}

export interface SchemeCard {
  slug: string;
  name: string;
  name_hi?: string | null;
  state?: string | null;
  category?: string | null;
  verdict: Verdict | null;
  reasons: string[];
  missing: string[];
  also_check?: string[];
  benefits: string;
  documents_required: string[];
  source_url: string | null;
  last_checked: string | null;
  verification: Verification | null;
}

export interface FollowUp {
  field: string;
  question: string;
  affects?: number;
  options?: string[];
}

export interface ChatFinal {
  session_id: string;
  lang: Lang;
  intent: "find_schemes" | "scheme_question";
  profile: Profile;
  answer: Answer;
  schemes: SchemeCard[];
  follow_up: FollowUp | null;
  cached: boolean;
  timings_ms?: Record<string, number>;
  trace_id?: string;
}

export type ChatEvent =
  | { type: "meta"; session_id: string; lang: Lang; intent: string; profile: Profile }
  | { type: "token"; text: string }
  | { type: "revise"; text: string }
  | ({ type: "final" } & ChatFinal)
  | { type: "error"; detail: string };

export interface EligibilityItem {
  slug: string;
  name: string;
  verdict: Verdict;
  reasons: string[];
  missing: string[];
  also_check?: string[];
  source_url: string | null;
  last_checked: string | null;
  verification: Verification | null;
}

export interface EligibilityResponse {
  profile: Profile;
  counts: Record<Verdict, number>;
  schemes: EligibilityItem[];
  follow_up: FollowUp | null;
}

export interface SchemeDetail {
  slug: string;
  name: string;
  name_hi?: string | null;
  ministry?: string | null;
  state: string;
  category?: string | null;
  benefits?: string | null;
  documents_required: string[];
  criteria: Record<string, unknown>;
  verification: Verification;
  status: string;
  source_url: string | null;
  pdf_url?: string | null;
  last_checked: string | null;
  data_credit?: string;
}

export interface UpdateRow {
  id: number;
  run_at: string;
  added: number;
  updated: number;
  inactive: number;
  details: { summary?: string; new?: string[]; updated?: string[]; inactive?: string[]; source?: string };
}

export interface VoiceResult extends Partial<ChatFinal> {
  transcript: string;
  audio_url?: string | null;
}
