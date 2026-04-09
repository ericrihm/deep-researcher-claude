"""Prompt templates for the ResearchAgent LLM calls."""

CATEGORIZE_PROMPT = """\
You are a research librarian. Below are {count} papers on: "{query}"

Assign each paper to exactly one category (3-6 categories). \
Categorize by approach/theme, NOT by database or year.

## Papers
{paper_list}

## Output Format
Return ONLY a list in this exact format (one line per category, paper numbers comma-separated):

CATEGORY: Category Name
PAPERS: 1, 5, 12, 23

CATEGORY: Another Category
PAPERS: 2, 7, 8, 19

Rules:
- Every paper number must appear in exactly one category
- 3-6 categories total
- Category names should be specific (e.g., "Vision-Based Damage Detection", not "Methods")
- Category names must be noun phrases, NOT verbs or actions.
  Bad: "Using CNNs for Detection", "Applying Transfer Learning"
  Good: "CNN-Based Defect Detection", "Transfer Learning Approaches"
- Aim for similarly sized categories — avoid one category with 80% of papers and others with 2-3
- No explanation needed — just the categories and paper numbers
"""

MERGE_CATEGORIES_PROMPT = """\
/no_think
You are a research librarian. Papers on "{query}" were categorized in batches, \
producing {count} overlapping categories. Merge them into {target} final categories \
by grouping semantically similar ones together.

## Current categories (name -> paper count)
{category_list}

## Output Format
Return ONLY a mapping in this exact format (one line per final category):

FINAL: Final Category Name
MERGE: Old Category A, Old Category B, Old Category C

FINAL: Another Final Category
MERGE: Old Category D, Old Category E

Rules:
- Exactly {target} final categories
- Every old category must appear in exactly one MERGE line
- Use the old category names exactly as listed above
- Final category names should be descriptive (not generic like "Other")
- Prefer merging the smallest categories first into larger ones
- A final category should have at least 5 papers when possible
"""

CATEGORY_SYNTHESIS_PROMPT = """\
You are a research analyst writing one section of a detailed literature review on: "{query}"

This section covers the category: **{category}** ({count} papers)

## Papers in this category
{corpus}

## Write this section. Reference papers by [number].

**What this group does:**
Write a paragraph (3-5 sentences) explaining the shared approach/theme.
Reference individual papers: e.g., "Smith et al. [1] introduced X. Jones et al. [2] extended this by Y."

**Key methods:**
Write a paragraph describing the specific methods and techniques.
For each method, cite which paper(s) used it.

**Main findings:**
Write a paragraph on collective findings. Include specific results ONLY if the \
abstract explicitly states them (e.g., accuracy percentages, performance metrics). \
Do NOT infer, generalize, or fabricate results that are not in the abstracts. \
If a paper's abstract does not mention specific metrics, write \
"The abstract does not report specific metrics" rather than omitting the paper or guessing.

**Limitations & gaps (your analysis):**
Write YOUR OWN analysis of common weaknesses and gaps across this group. \
This is your synthesis — do NOT attribute these observations to specific papers \
with [number] citations. Instead write: "A common limitation across these studies is..." \
or "This group does not address..."

| Ref | Paper | Year | Method | Key Finding | Citations |
|-----|-------|------|--------|-------------|-----------|
(Include EVERY paper listed above in the table. Sort rows by Year, newest first. \
If a paper has no citation count, write "-" not "0".)

## CRITICAL RULES
- ONLY state what the abstracts explicitly say. If a metric is not in the abstract, do NOT invent it.
- When citing [N], the claim MUST come from that paper's abstract above. Verify before writing.
- The Limitations section is YOUR analysis — do NOT fake-attribute observations to papers.
- Include ALL papers from this category in the table.
- Write in active voice. Avoid passive constructions like "was proposed" or "was found to be".
- Be direct. No filler. No "In recent years..."
- Do NOT write references or cross-category analysis — just this one section.
"""

CROSS_CATEGORY_PROMPT = """\
You are a research analyst. You've categorized papers on "{query}" into these groups:

{category_summaries}

Now write ONLY these sections:

#### Cross-Category Patterns
What patterns emerge across categories? Which are converging? \
What contradictions exist? Which papers bridge multiple categories? \
Name at least 3 specific paper numbers when discussing each pattern.

#### Gaps & Opportunities
Be specific. Name concrete research questions nobody has addressed. \
Point to specific method/domain combinations that haven't been tried. \
For each gap, explain WHY it matters — don't just list missing topics.

#### Open Access Papers
List any papers with free full-text URLs mentioned above.

Rules:
- Be direct and specific — no vague generalities
- Reference specific paper numbers when possible
- Do NOT repeat the per-category analysis
"""

CLARIFY_PROMPT = """\
You are a research assistant helping to refine a research question before searching academic databases.

Given the user's research topic, generate exactly 2 short, focused clarifying questions that would \
help narrow the search and produce better results. Focus on the most important dimensions:
- Specific subfield or application domain
- Methodological focus (theoretical, empirical, computational, etc.)

Frame questions as quick multiple-choice when possible. \
Example: "Which aspect matters most? (a) clinical trials  (b) computational models  (c) both equally"

Format: Return ONLY the 2 questions, one per line, numbered 1-2. No preamble.
"""

EXECUTIVE_SUMMARY_PROMPT = """\
You are writing a 100-150 word executive summary for a literature review on: "{query}"

The review covers {count} papers organized into {cat_count} categories:
{category_list}

Below are the top {top_n} most-cited papers in the corpus, for grounding:
{top_papers}

Write a single paragraph (100-150 words) that a busy researcher could read in
30 seconds to know whether this review is useful to them. Cover:
  1. The scope of the corpus (how many papers, what time range, which sub-areas)
  2. The dominant approach or finding that emerges across categories
  3. The biggest open question or gap

Rules:
- Start with the most surprising or important finding from the corpus — NOT with \
  a description of what the review contains
- Use concrete numbers from the corpus \
  (e.g., "62 of 100 papers use transformer architectures", "only 3 papers test on real-world data")
- ONE paragraph. No headings. No bullet points.
- Do NOT cite specific papers with [N] — this is a high-level summary.
- Do NOT invent findings that aren't supported by the category list or top papers.
- Do NOT start with "This review..." or "In recent years..." — get to the point.
"""
