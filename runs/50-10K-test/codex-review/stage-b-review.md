# Stage B — Codex's per-row review

**Model:** gpt-5.5 (Codex v0.128.0)
**Input given:** Full 50-row CSV including the Notes column (with iter-R labels visible) + Stage A's 15 criteria pasted back verbatim.
**Tokens used:** ~38K

---

ROW 1 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 2 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 3 — verdict: PASS
Failed criteria (if any): none
Notes: Extra `affiliations` keys do not violate schema.

ROW 4 — verdict: PASS
Failed criteria (if any): none
Notes: Paywalled row still has authors and resolved link.

ROW 5 — verdict: PASS
Failed criteria (if any): none
Notes: PDF URL is present but `Resolves To PDF=FALSE`; acceptable because landing link is non-PDF.

ROW 6 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Abstract has repeated `â...â` encoding artifacts.

ROW 7 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake), #12 (keywords/DOI appendage)
Notes: Abstract includes KEY WORDS, journal citation, and DOI.

ROW 8 — verdict: FAIL-MEDIUM
Failed criteria (if any): #12 (availability/contact appendage)
Notes: Abstract includes Availability and Contact sections.

ROW 9 — verdict: PASS
Failed criteria (if any): none
Notes: No PDF URL, but metadata fields are otherwise substantive.

ROW 10 — verdict: PASS
Failed criteria (if any): none
Notes: Clean paywalled extraction.

ROW 11 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 12 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Abstract has `â...â` artifacts and is truncated.

ROW 13 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 14 — verdict: PASS
Failed criteria (if any): none
Notes: PDF redirect row is internally consistent.

ROW 15 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Abstract and one affiliation contain repeated encoding artifacts.

ROW 16 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 17 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but internally valid.

ROW 18 — verdict: PASS
Failed criteria (if any): none
Notes: Truncated abstract, but still plausible prose.

ROW 19 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check flag is consistent with anti-bot resolved link.

ROW 20 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 21 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 22 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

ROW 23 — verdict: PASS
Failed criteria (if any): none
Notes: Abstract prose is awkward but plausible.

ROW 24 — verdict: FAIL-HIGH
Failed criteria (if any): #2 (missing resolved link)
Notes: `broken_doi=FALSE` but `resolved_links` is empty.

ROW 25 — verdict: PASS
Failed criteria (if any): none
Notes: Truncated abstract, but plausible.

ROW 26 — verdict: FAIL-MEDIUM
Failed criteria (if any): #15 (non-English abstract flagged false)
Notes: Spanish abstract with `no english=FALSE`.

ROW 27 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 28 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 29 — verdict: PASS
Failed criteria (if any): none
Notes: Authors only, but still internally valid.

ROW 30 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 31 — verdict: PASS
Failed criteria (if any): none
Notes: PDF URL present and valid.

ROW 32 — verdict: FAIL-HIGH
Failed criteria (if any): #2 (missing resolved link)
Notes: `broken_doi=FALSE` but `resolved_links` is empty.

ROW 33 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

ROW 34 — verdict: FAIL-CRITICAL
Failed criteria (if any): #6 (author object missing `corresponding_author`)
Notes: Second author object lacks required boolean field.

ROW 35 — verdict: PASS
Failed criteria (if any): none
Notes: Short abstract is plausible.

ROW 36 — verdict: PASS
Failed criteria (if any): none
Notes: Truncated abstract, but plausible.

ROW 37 — verdict: PASS
Failed criteria (if any): none
Notes: Copyright notice is minor and not dominant.

ROW 38 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Abstract has `Î` artifacts.

ROW 39 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 40 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 41 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 42 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 43 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 44 — verdict: PASS
Failed criteria (if any): none
Notes: Abstract is awkward but plausible English prose.

ROW 45 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

ROW 46 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Affiliations contain `â...â` artifacts.

ROW 47 — verdict: FAIL-MEDIUM
Failed criteria (if any): #15 (non-English abstract flagged false)
Notes: Spanish abstract with `no english=FALSE`.

ROW 48 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

ROW 49 — verdict: PASS
Failed criteria (if any): none
Notes: PDF route is valid and PDF-like.

ROW 50 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

## AGGREGATE
- Critical failures: 1 across rows 34
- High failures: 2 across rows 24, 32
- Medium failures: 8 across rows 6, 7, 8, 12, 15, 26, 38, 46, 47
- Approval verdict: REJECTED
- Top 3 systemic patterns: Mojibake in abstracts/affiliations is the most common quality issue.
- Top 3 systemic patterns: Resolve-error rows can retain metadata while missing `resolved_links`.
- Top 3 systemic patterns: Non-English abstracts are not reliably setting `no english=TRUE`.
tokens used
38,042
ROW 1 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 2 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 3 — verdict: PASS
Failed criteria (if any): none
Notes: Extra `affiliations` keys do not violate schema.

ROW 4 — verdict: PASS
Failed criteria (if any): none
Notes: Paywalled row still has authors and resolved link.

ROW 5 — verdict: PASS
Failed criteria (if any): none
Notes: PDF URL is present but `Resolves To PDF=FALSE`; acceptable because landing link is non-PDF.

ROW 6 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Abstract has repeated `â...â` encoding artifacts.

ROW 7 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake), #12 (keywords/DOI appendage)
Notes: Abstract includes KEY WORDS, journal citation, and DOI.

ROW 8 — verdict: FAIL-MEDIUM
Failed criteria (if any): #12 (availability/contact appendage)
Notes: Abstract includes Availability and Contact sections.

ROW 9 — verdict: PASS
Failed criteria (if any): none
Notes: No PDF URL, but metadata fields are otherwise substantive.

ROW 10 — verdict: PASS
Failed criteria (if any): none
Notes: Clean paywalled extraction.

ROW 11 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 12 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Abstract has `â...â` artifacts and is truncated.

ROW 13 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 14 — verdict: PASS
Failed criteria (if any): none
Notes: PDF redirect row is internally consistent.

ROW 15 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Abstract and one affiliation contain repeated encoding artifacts.

ROW 16 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 17 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but internally valid.

ROW 18 — verdict: PASS
Failed criteria (if any): none
Notes: Truncated abstract, but still plausible prose.

ROW 19 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check flag is consistent with anti-bot resolved link.

ROW 20 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 21 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 22 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

ROW 23 — verdict: PASS
Failed criteria (if any): none
Notes: Abstract prose is awkward but plausible.

ROW 24 — verdict: FAIL-HIGH
Failed criteria (if any): #2 (missing resolved link)
Notes: `broken_doi=FALSE` but `resolved_links` is empty.

ROW 25 — verdict: PASS
Failed criteria (if any): none
Notes: Truncated abstract, but plausible.

ROW 26 — verdict: FAIL-MEDIUM
Failed criteria (if any): #15 (non-English abstract flagged false)
Notes: Spanish abstract with `no english=FALSE`.

ROW 27 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 28 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 29 — verdict: PASS
Failed criteria (if any): none
Notes: Authors only, but still internally valid.

ROW 30 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 31 — verdict: PASS
Failed criteria (if any): none
Notes: PDF URL present and valid.

ROW 32 — verdict: FAIL-HIGH
Failed criteria (if any): #2 (missing resolved link)
Notes: `broken_doi=FALSE` but `resolved_links` is empty.

ROW 33 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

ROW 34 — verdict: FAIL-CRITICAL
Failed criteria (if any): #6 (author object missing `corresponding_author`)
Notes: Second author object lacks required boolean field.

ROW 35 — verdict: PASS
Failed criteria (if any): none
Notes: Short abstract is plausible.

ROW 36 — verdict: PASS
Failed criteria (if any): none
Notes: Truncated abstract, but plausible.

ROW 37 — verdict: PASS
Failed criteria (if any): none
Notes: Copyright notice is minor and not dominant.

ROW 38 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Abstract has `Î` artifacts.

ROW 39 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 40 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 41 — verdict: PASS
Failed criteria (if any): none
Notes: Bot-check row with empty extraction is consistent.

ROW 42 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 43 — verdict: PASS
Failed criteria (if any): none
Notes: Clean extraction.

ROW 44 — verdict: PASS
Failed criteria (if any): none
Notes: Abstract is awkward but plausible English prose.

ROW 45 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

ROW 46 — verdict: FAIL-MEDIUM
Failed criteria (if any): #11 (mojibake)
Notes: Affiliations contain `â...â` artifacts.

ROW 47 — verdict: FAIL-MEDIUM
Failed criteria (if any): #15 (non-English abstract flagged false)
Notes: Spanish abstract with `no english=FALSE`.

ROW 48 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

ROW 49 — verdict: PASS
Failed criteria (if any): none
Notes: PDF route is valid and PDF-like.

ROW 50 — verdict: PASS
Failed criteria (if any): none
Notes: Sparse but valid.

## AGGREGATE
- Critical failures: 1 across rows 34
- High failures: 2 across rows 24, 32
- Medium failures: 8 across rows 6, 7, 8, 12, 15, 26, 38, 46, 47
- Approval verdict: REJECTED
- Top 3 systemic patterns: Mojibake in abstracts/affiliations is the most common quality issue.
- Top 3 systemic patterns: Resolve-error rows can retain metadata while missing `resolved_links`.
- Top 3 systemic patterns: Non-English abstracts are not reliably setting `no english=TRUE`.
