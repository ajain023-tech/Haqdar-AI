// Mock API built from the frozen contract, so UI work never waits for the backend.
// Used when NEXT_PUBLIC_API_URL is empty or NEXT_PUBLIC_USE_MOCK=1.
import type {
  ChatEvent, EligibilityResponse, Lang, Profile, SchemeCard, SchemeDetail, UpdateRow, VoiceResult,
} from "./types";

const CHECKED = "2026-11-27T05:00:00+00:00";

const SCHEMES: SchemeDetail[] = [
  {
    slug: "pm-kisan", name: "Pradhan Mantri Kisan Samman Nidhi", name_hi: "प्रधानमंत्री किसान सम्मान निधि",
    ministry: "Ministry Of Agriculture and Farmers Welfare", state: "ALL", category: "agriculture",
    benefits: "₹6,000 per year per farmer family, paid in three instalments of ₹2,000 directly to the bank account.",
    documents_required: ["Aadhaar Card", "Landholding papers", "Savings Bank Account"],
    criteria: { occupation: ["farmer"] }, verification: "verified", status: "active",
    source_url: "https://www.myscheme.gov.in/schemes/pm-kisan", last_checked: CHECKED,
  },
  {
    slug: "pmuy", name: "Pradhan Mantri Ujjwala Yojana", name_hi: "प्रधानमंत्री उज्ज्वला योजना",
    ministry: "Ministry Of Petroleum and Natural Gas", state: "ALL", category: "women_child",
    benefits: "Deposit-free LPG connection, first refill and stove for women from poor households.",
    documents_required: ["KYC form", "Aadhaar card", "Ration card", "Bank account number and IFSC"],
    criteria: { gender: "female", age: { min: 18 }, bpl: true }, verification: "auto", status: "active",
    source_url: "https://www.myscheme.gov.in/schemes/pmuy", last_checked: CHECKED,
  },
  {
    slug: "ignwps", name: "Indira Gandhi National Widow Pension Scheme", name_hi: "इंदिरा गांधी राष्ट्रीय विधवा पेंशन योजना",
    ministry: "Ministry Of Rural Development", state: "ALL", category: "pension",
    benefits: "Monthly pension for widows aged 40–79 from BPL households.",
    documents_required: ["Aadhaar card", "BPL card", "Husband's death certificate", "Bank passbook"],
    criteria: { gender: "female", age: { min: 40, max: 79 }, bpl: true }, verification: "stale", status: "active",
    source_url: "https://www.myscheme.gov.in/schemes/ignwps", last_checked: "2026-10-20T05:00:00+00:00",
  },
  {
    slug: "ksy", name: "Kanya Sumangala Yojana", name_hi: "कन्या सुमंगला योजना", ministry: null,
    state: "Uttar Pradesh", category: "women_child",
    benefits: "₹25,000 in six phases for a girl child, from birth to graduation, for UP families earning up to ₹3 lakh.",
    documents_required: ["Photo of the girl child", "Birth certificate", "Income certificate", "Residence proof"],
    criteria: { states: ["Uttar Pradesh"], annual_income: { max: 300000 } }, verification: "auto",
    status: "active", source_url: "https://www.myscheme.gov.in/schemes/ksy", last_checked: CHECKED,
  },
];

function card(s: SchemeDetail, profile: Profile): SchemeCard {
  const missing: string[] = [];
  const reasons: string[] = [];
  let verdict: SchemeCard["verdict"] = "eligible";
  const c = s.criteria as Record<string, any>;
  if (c.gender) {
    if (!profile.gender) missing.push("gender");
    else if (profile.gender !== c.gender) { verdict = "no"; reasons.push(`only for ${c.gender} applicants`); }
  }
  if (c.age) {
    if (profile.age == null) missing.push("age");
    else if ((c.age.min && profile.age < c.age.min) || (c.age.max && profile.age > c.age.max)) {
      verdict = "no"; reasons.push(`age ${profile.age} is outside ${c.age.min ?? 0}–${c.age.max ?? "∞"}`);
    } else reasons.push(`age ${profile.age} is within the limit`);
  }
  if (c.bpl && profile.bpl == null) missing.push("bpl");
  if (c.occupation) {
    if (!profile.occupation) missing.push("occupation");
    else if (!c.occupation.includes(profile.occupation)) { verdict = "no"; reasons.push(`must be a ${c.occupation[0]}`); }
  }
  if (verdict !== "no" && missing.length) verdict = "maybe";
  return {
    slug: s.slug, name: s.name, name_hi: s.name_hi, state: s.state, category: s.category, verdict, reasons, missing,
    benefits: s.benefits ?? "", documents_required: s.documents_required, source_url: s.source_url,
    last_checked: s.last_checked, verification: s.verification,
  };
}

function guessProfile(text: string): Profile {
  const p: Profile = {};
  const low = text.toLowerCase();
  const age = low.match(/(\d{1,3})\s*(years?|saal|साल|वर्ष)/);
  if (age) p.age = Number(age[1]);
  if (/widow|vidhwa|विधवा|mahila|woman|female|महिला/.test(low)) p.gender = "female";
  if (/farmer|kisan|किसान/.test(low)) p.occupation = "farmer";
  if (/\bup\b|uttar pradesh|उत्तर प्रदेश/.test(low)) p.state = "Uttar Pradesh";
  if (/bpl|बीपीएल/.test(low)) p.bpl = !/nahi|नहीं|no bpl/.test(low);
  return p;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function* mockChat(message: string, lang: Lang, sessionId?: string, profile?: Profile)
  : AsyncGenerator<ChatEvent> {
  const session_id = sessionId ?? crypto.randomUUID();
  const prof = { ...guessProfile(message), ...(profile ?? {}) };
  const trick = /gold coin|smartphone|laptop|washing machine|मुफ्त स्मार्टफोन/i.test(message);
  yield { type: "meta", session_id, lang, intent: trick ? "scheme_question" : "find_schemes", profile: prof };
  await sleep(250);
  const refusal = lang === "hi"
    ? "मुझे यह जानकारी आधिकारिक स्रोतों में नहीं मिली। कृपया myscheme.gov.in देखें।"
    : lang === "hinglish"
      ? "Mujhe yeh jaankari official sources mein nahi mili. Kripya myscheme.gov.in dekhein."
      : "I couldn't find this in official sources. Please check myscheme.gov.in.";
  const cards = trick ? [] : SCHEMES.map((s) => card(s, prof)).filter((c) => c.verdict !== "no");
  const text = trick ? refusal : (lang === "hi"
    ? `आपकी जानकारी के आधार पर ${cards.length} योजनाएँ मिलीं। प्रधानमंत्री किसान सम्मान निधि में किसान परिवारों को सालाना ₹6,000 मिलते हैं [1]।`
    : `Based on what you told me, ${cards.length} schemes may fit. PM-KISAN pays farmer families ₹6,000 a year in three instalments [1].`);
  for (const word of text.split(/(\s+)/)) {
    yield { type: "token", text: word };
    await sleep(25);
  }
  const needsBpl = cards.some((c) => c.missing.includes("bpl"));
  yield {
    type: "final", session_id, lang, intent: trick ? "scheme_question" : "find_schemes", profile: prof, cached: false,
    answer: {
      text, lang, refused: trick,
      citations: trick ? [] : [{ n: 1, slug: "pm-kisan", name: SCHEMES[0].name, section: "benefits",
        source_url: SCHEMES[0].source_url, last_checked: CHECKED, verification: "verified",
        snippet: SCHEMES[0].benefits ?? "" }],
      disclaimer: "Unofficial assistant. Final eligibility is decided by the department; apply via the official link.",
    },
    schemes: cards,
    follow_up: !trick && needsBpl ? { field: "bpl", question: lang === "hi" ? "क्या आपके परिवार के पास बीपीएल कार्ड है?"
      : "Does your family have a BPL card?", options: ["yes", "no"], affects: 2 } : null,
  };
}

export async function mockEligibility(profile: Profile): Promise<EligibilityResponse> {
  await sleep(200);
  const items = SCHEMES.map((s) => card(s, profile)).map((c) => ({
    slug: c.slug, name: c.name, verdict: c.verdict ?? "maybe", reasons: c.reasons, missing: c.missing,
    source_url: c.source_url, last_checked: c.last_checked, verification: c.verification,
  }));
  const counts = { eligible: 0, maybe: 0, no: 0 } as EligibilityResponse["counts"];
  items.forEach((i) => counts[i.verdict]++);
  return { profile, counts, schemes: items, follow_up: null };
}

export async function mockScheme(slug: string): Promise<SchemeDetail | null> {
  await sleep(100);
  return SCHEMES.find((s) => s.slug === slug) ?? null;
}

export async function mockUpdates(): Promise<UpdateRow[]> {
  return [
    { id: 2, run_at: "2026-11-27T22:00:00+00:00", added: 1, updated: 3, inactive: 0,
      details: { summary: "3 updated, 1 new, 0 inactive", updated: ["pm-kisan", "pmuy", "ksy"], new: ["pm-svanidhi"] } },
    { id: 1, run_at: "2026-11-26T22:00:00+00:00", added: 0, updated: 1, inactive: 1,
      details: { summary: "1 updated, 0 new, 1 inactive", updated: ["ignwps"], inactive: ["old-scheme"] } },
  ];
}

export async function mockVoice(transcript?: string): Promise<VoiceResult> {
  await sleep(400);
  return { transcript: transcript || "मैं उत्तर प्रदेश का किसान हूँ, मेरी उम्र 45 साल है" };
}
