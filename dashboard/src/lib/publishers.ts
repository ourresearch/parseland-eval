import type { PerPublisher } from "./schema";

// Canonical publisher list. The first 10 are Casey's pinned top-10 (4/21 PM sync):
// Elsevier → Springer Nature → Taylor & Francis → Wiley → SAGE → Wolters Kluwer
// → OUP → CUP → IEEE → ACS. Rationale: scale-weighted impact — one template fix
// at a major publisher = millions of articles, vs. thousands at a small one.

export const PINNED_PUBLISHERS = [
  "Elsevier",
  "Springer Nature",
  "Taylor & Francis",
  "Wiley",
  "SAGE",
  "Wolters Kluwer",
  "Oxford University Press",
  "Cambridge University Press",
  "IEEE",
  "American Chemical Society",
] as const;

export type PinnedPublisher = (typeof PINNED_PUBLISHERS)[number];
export type Publisher = string;

export const UNKNOWN_PUBLISHER = "Other / Unknown";

// Crossref member DOI prefix → publisher. Covers every prefix observed in the
// current 7 runs plus the major prefixes for each of Casey's top-10.
// Source: Crossref REST API member records + publisher web presence. Keep in
// sync as the gold standard grows; unknown prefixes fall through to "Other / Unknown".
const PREFIX_TO_PUBLISHER: Record<string, Publisher> = {
  // Elsevier (incl. legacy Cell Press, Academic Press)
  "10.1016": "Elsevier",
  "10.1006": "Elsevier",
  "10.1053": "Elsevier",
  "10.1067": "Elsevier",
  "10.1078": "Elsevier",

  // Springer Nature (incl. Nature Publishing Group, BMC, Palgrave Macmillan)
  "10.1007": "Springer Nature",
  "10.1038": "Springer Nature",
  "10.1186": "Springer Nature",
  "10.1057": "Springer Nature",
  "10.1140": "Springer Nature",
  "10.1361": "Springer Nature",

  // Taylor & Francis (Informa)
  "10.1080": "Taylor & Francis",
  "10.4324": "Taylor & Francis",
  "10.1081": "Taylor & Francis",
  "10.1201": "Taylor & Francis",

  // Wiley (Wiley-Blackwell)
  "10.1002": "Wiley",
  "10.1111": "Wiley",
  "10.1046": "Wiley",
  "10.1113": "Wiley",
  "10.1034": "Wiley",

  // SAGE
  "10.1177": "SAGE",
  "10.4135": "SAGE",
  "10.1191": "SAGE",

  // Wolters Kluwer (Lippincott Williams & Wilkins)
  "10.1097": "Wolters Kluwer",
  "10.1213": "Wolters Kluwer",
  "10.1212": "Wolters Kluwer",

  // Oxford University Press
  "10.1093": "Oxford University Press",
  "10.1001": "Oxford University Press", // some AMA/OUP overlap — AMA below wins if present
  "10.1215": "Oxford University Press",

  // Cambridge University Press
  "10.1017": "Cambridge University Press",
  "10.1079": "Cambridge University Press",

  // IEEE
  "10.1109": "IEEE",

  // American Chemical Society
  "10.1021": "American Chemical Society",

  // --- Below Casey's top-10 (tail, alphabetical within tiers) ---

  // Society / learned-society publishers
  "10.1103": "American Physical Society",
  "10.1063": "AIP Publishing",
  "10.1121": "Acoustical Society of America",
  "10.1039": "Royal Society of Chemistry",
  "10.1136": "BMJ",
  "10.1055": "Thieme",
  "10.1088": "IOP Publishing",
  "10.1042": "Portland Press",
  "10.1042/BCJ": "Portland Press",

  // OA / megajournals
  "10.1371": "PLOS",
  "10.3390": "MDPI",
  "10.3389": "Frontiers",
  "10.1101": "Cold Spring Harbor",

  // Other named presses
  "10.1086": "University of Chicago Press",
  "10.1108": "Emerald",
  "10.1163": "Brill",
  "10.1515": "De Gruyter",
  "10.2307": "JSTOR",
  "10.36838": "IDEAS",
  "10.3791": "MyJoVE",
  "10.7256": "PeerJ",

  // Medical associations
  "10.1161": "American Heart Association",
  "10.1001/jama": "American Medical Association",

  // Pharma / biochem
  "10.1124": "ASPET",
  "10.1128": "American Society for Microbiology",
  "10.1158": "AACR",

  // Open-access / nonprofit
  "10.7717": "PeerJ",
  "10.1105": "ASPB",
};

// Some prefixes are shared across multiple presses; we fall back to hostname
// heuristics when a prefix maps ambiguously. This table is consulted after
// the prefix lookup when the prefix is not in PREFIX_TO_PUBLISHER.
const HOST_TO_PUBLISHER: Array<{ pattern: RegExp; publisher: Publisher }> = [
  { pattern: /(^|\.)sciencedirect\.com$/i, publisher: "Elsevier" },
  { pattern: /(^|\.)cell\.com$/i, publisher: "Elsevier" },
  { pattern: /(^|\.)thelancet\.com$/i, publisher: "Elsevier" },
  { pattern: /(^|\.)link\.springer\.com$/i, publisher: "Springer Nature" },
  { pattern: /(^|\.)nature\.com$/i, publisher: "Springer Nature" },
  { pattern: /(^|\.)biomedcentral\.com$/i, publisher: "Springer Nature" },
  { pattern: /(^|\.)tandfonline\.com$/i, publisher: "Taylor & Francis" },
  { pattern: /(^|\.)onlinelibrary\.wiley\.com$/i, publisher: "Wiley" },
  { pattern: /(^|\.)journals\.sagepub\.com$/i, publisher: "SAGE" },
  { pattern: /(^|\.)journals\.lww\.com$/i, publisher: "Wolters Kluwer" },
  { pattern: /(^|\.)academic\.oup\.com$/i, publisher: "Oxford University Press" },
  { pattern: /(^|\.)cambridge\.org$/i, publisher: "Cambridge University Press" },
  { pattern: /(^|\.)ieeexplore\.ieee\.org$/i, publisher: "IEEE" },
  { pattern: /(^|\.)pubs\.acs\.org$/i, publisher: "American Chemical Society" },
];

export function extractDoiPrefix(doi: string | null | undefined): string | null {
  if (!doi) return null;
  const slash = doi.indexOf("/");
  if (slash < 0) return null;
  return doi.slice(0, slash);
}

function hostOf(link: string | null | undefined): string | null {
  if (!link) return null;
  try {
    return new URL(link).hostname;
  } catch {
    return null;
  }
}

export function resolvePublisher(
  doi: string | null | undefined,
  link?: string | null | undefined,
): Publisher {
  const prefix = extractDoiPrefix(doi);
  if (prefix) {
    // Try prefix + full path (e.g., "10.1001/jama") for the rare disambiguation cases.
    const sub = doi!.split("/", 2).join("/");
    if (PREFIX_TO_PUBLISHER[sub]) return PREFIX_TO_PUBLISHER[sub];
    if (PREFIX_TO_PUBLISHER[prefix]) return PREFIX_TO_PUBLISHER[prefix];
  }
  const host = hostOf(link);
  if (host) {
    for (const { pattern, publisher } of HOST_TO_PUBLISHER) {
      if (pattern.test(host)) return publisher;
    }
  }
  return UNKNOWN_PUBLISHER;
}

// Aggregation from per-row scores, because current runs have publisher_domain="doi.org"
// for all rows (only the DOI prefix actually differentiates). This computes the same
// PerPublisher shape the heatmap expects, but keyed by publisher entity instead of
// hostname, so existing renderers can reuse it.
export interface RowForAggregation {
  doi: string;
  link?: string | null;
  publisher_domain?: string;
  score: {
    authors: {
      precision_soft?: number;
      recall_soft?: number;
      f1_soft: number;
    } | null;
    affiliations: {
      fuzzy_f1: number;
      fuzzy_precision?: number;
      fuzzy_recall?: number;
    } | null;
    abstract: {
      fuzzy_ratio: number;
      match_at_threshold?: boolean;
    };
    pdf_url: {
      strict_match: boolean;
      present: boolean;
      expected_present: boolean;
      divergent: boolean;
    };
    error: string | null;
  };
}

interface Accum {
  rows: number;
  authors_scored_rows: number;
  authors_p_sum: number;
  authors_r_sum: number;
  authors_f1_sum: number;
  aff_scored_rows: number;
  aff_f1_sum: number;
  aff_p_sum: number;
  aff_r_sum: number;
  abstract_ratio_sum: number;
  abstract_match_hits: number;
  pdf_tp: number;
  pdf_fp: number;
  pdf_fn: number;
  pdf_strict_hits: number;
  pdf_expected_rows: number;
  errors: number;
}

function emptyAccum(): Accum {
  return {
    rows: 0,
    authors_scored_rows: 0,
    authors_p_sum: 0,
    authors_r_sum: 0,
    authors_f1_sum: 0,
    aff_scored_rows: 0,
    aff_f1_sum: 0,
    aff_p_sum: 0,
    aff_r_sum: 0,
    abstract_ratio_sum: 0,
    abstract_match_hits: 0,
    pdf_tp: 0,
    pdf_fp: 0,
    pdf_fn: 0,
    pdf_strict_hits: 0,
    pdf_expected_rows: 0,
    errors: 0,
  };
}

export function groupByPublisher(rows: RowForAggregation[]): PerPublisher {
  const buckets = new Map<Publisher, Accum>();

  for (const row of rows) {
    const publisher = resolvePublisher(row.doi, row.link);
    let acc = buckets.get(publisher);
    if (!acc) {
      acc = emptyAccum();
      buckets.set(publisher, acc);
    }
    acc.rows += 1;
    if (row.score.error) acc.errors += 1;

    const a = row.score.authors;
    if (a) {
      acc.authors_scored_rows += 1;
      acc.authors_f1_sum += a.f1_soft;
      acc.authors_p_sum += a.precision_soft ?? a.f1_soft;
      acc.authors_r_sum += a.recall_soft ?? a.f1_soft;
    }

    const af = row.score.affiliations;
    if (af) {
      acc.aff_scored_rows += 1;
      acc.aff_f1_sum += af.fuzzy_f1;
      acc.aff_p_sum += af.fuzzy_precision ?? af.fuzzy_f1;
      acc.aff_r_sum += af.fuzzy_recall ?? af.fuzzy_f1;
    }

    acc.abstract_ratio_sum += row.score.abstract.fuzzy_ratio;
    if (row.score.abstract.match_at_threshold) acc.abstract_match_hits += 1;

    const pdf = row.score.pdf_url;
    if (pdf.expected_present) acc.pdf_expected_rows += 1;
    if (pdf.strict_match) acc.pdf_strict_hits += 1;
    // Treat PDF URL as a precision/recall problem.
    if (pdf.present && pdf.expected_present && pdf.strict_match) acc.pdf_tp += 1;
    if (pdf.present && (!pdf.expected_present || !pdf.strict_match)) acc.pdf_fp += 1;
    if (pdf.expected_present && !pdf.strict_match) acc.pdf_fn += 1;
  }

  const out: PerPublisher = {};
  for (const [publisher, acc] of buckets.entries()) {
    const scoredA = acc.authors_scored_rows || 1;
    const scoredAf = acc.aff_scored_rows || 1;
    const pdfDenomP = acc.pdf_tp + acc.pdf_fp || 1;
    const pdfDenomR = acc.pdf_tp + acc.pdf_fn || 1;
    out[publisher] = {
      rows: acc.rows,
      authors_f1_soft: acc.authors_f1_sum / scoredA,
      authors_precision_soft: acc.authors_p_sum / scoredA,
      authors_recall_soft: acc.authors_r_sum / scoredA,
      affiliations_f1_fuzzy: acc.aff_f1_sum / scoredAf,
      affiliations_precision_fuzzy: acc.aff_p_sum / scoredAf,
      affiliations_recall_fuzzy: acc.aff_r_sum / scoredAf,
      abstract_ratio_fuzzy: acc.abstract_ratio_sum / acc.rows,
      abstract_match_rate: acc.abstract_match_hits / acc.rows,
      pdf_url_accuracy: acc.pdf_strict_hits / acc.rows,
      pdf_url_precision: acc.pdf_tp / pdfDenomP,
      pdf_url_recall: acc.pdf_tp / pdfDenomR,
      errors: acc.errors,
    };
  }
  return out;
}

// Returns the publisher buckets ordered with Casey's pinned top-10 first (in
// the fixed canonical order), then tail sorted by row count descending. This
// is the ordering Casey's 4/21 PM sync specified: scale-weighted impact.
export function orderedPublishers(data: PerPublisher): Array<[Publisher, PerPublisher[string]]> {
  const entries = Object.entries(data);
  const pinnedSet = new Set<string>(PINNED_PUBLISHERS);

  const pinned = PINNED_PUBLISHERS
    .map((name) => {
      const found = entries.find(([pub]) => pub === name);
      return found ? ([name as string, found[1]] as const) : null;
    })
    .filter((x): x is readonly [string, PerPublisher[string]] => x !== null)
    .map((x) => [x[0], x[1]] as [Publisher, PerPublisher[string]]);

  const tail = entries
    .filter(([pub]) => !pinnedSet.has(pub))
    .sort((a, b) => b[1].rows - a[1].rows);

  return [...pinned, ...tail];
}
