# NutriQA dataset tools

A Python command-line application for converting, cleaning, deduplicating, and screening
the NutriQA dataset. Conversion uses only the `Arabic Dataset` sheet in
`data/NutriQA-Ar.xlsx`. Other workbook sheets, including the `Deduplication Log`,
are not read by these commands.

## Install

From the project directory, install the app in editable mode:

```powershell
python -m pip install -e 
```

## Commands

Convert the workbook's `Arabic Dataset` sheet to JSON. The input path is
optional; omitting it uses `data/NutriQA-Ar.xlsx`. The output defaults to
`data/NutriQA-Ar.json`:

```powershell
nutriqa convert to-json
```

Convert the default JSON file back to an Excel workbook. The output defaults to
`data/NutriQA-Ar.xlsx`:

```powershell
nutriqa convert from-json
```

Use `--output`/`-o` to choose another destination. Existing files require
interactive confirmation; `--force` replaces them without a prompt.

```powershell
nutriqa convert to-json data/NutriQA-Ar.xlsx -o output/nutriqa.json
nutriqa convert from-json output/nutriqa.json -o output/NutriQA-Ar.xlsx --force
```

Run `nutriqa --help`, `nutriqa convert --help`,
`nutriqa deduplicate --help`, `nutriqa report --help`, or
`nutriqa screen --help`, or `nutriqa clean --help` for command help.

### Clean an enriched dataset

Run the cleaner on the latest enriched v2 JSON file. It writes a new cleaned
dataset and updates the supplied issues workbook in place with `Cleaning
status` and `Cleaning action` columns on the `Findings` and `Source Rows` sheets:

```powershell
nutriqa clean
```

The issues workbook is the review queue. The command reads the record IDs from
its `Findings` and `Source Rows` sheets, then uses the question and answer text
to decide each case; it does not trust the workbook's finding labels. Only
listed records are cleaned or semantically reviewed. Other dataset records are
copied unchanged into the complete output JSON. The source JSON is streamed to
write that output, but records outside the issue queue are not sent for semantic
review.

For queued records, the command removes URLs, reference sections, and English
duplicate text when it matches the separate translation. GPT-6 Luna evaluates
nutrition relevance, question completeness, answer completeness, and whether
the answer addresses the question. High-confidence unrelated or incomplete
questions and records without an Arabic answer are deleted. False-positive
mismatch findings are removed from the issues workbook.

For a high-confidence incomplete or mismatched answer, the command searches
Altibbi's public medical Q&A index. It applies a correction only when an
Altibbi result directly answers the question; the concise Arabic answer and
its English translation are written to the dataset, and the source URL is
recorded in the issues workbook. If no sufficiently relevant source answer is
found, the row is deleted. Low-confidence cases remain in the dataset and are
marked `Need review`. If Altibbi is temporarily unavailable, the existing answer
is retained and the row is marked `Need review` instead of being deleted.

Semantic decisions and Altibbi searches for queued records are cached in
`data/.nutriqa-clean-cache.sqlite3` so an interrupted run can resume. Semantic
review uses the configured OpenAI API and may incur charges; Altibbi searches
require an internet connection. The default output is
`data/NutriQA-Ar-v2-deduplicated-cleaned.json`. Use `--issues`, `--output`,
`--semantic-cache`, and `--concurrency` to select paths and parallelism, and
`--force` to replace an existing output file.

To process only records whose workbook `Cleaning status` is `Need review`, run:

```powershell
nutriqa clean --review-only --force
```

This mode applies the cleaning rules only to those workbook IDs. It cleans
URLs, reference sections, and duplicate English text; evaluates relevance and
completeness; repairs incomplete or mismatched answers through Altibbi when a
direct answer is found; and deletes rows covered by the configured deletion
rules. Non-review workbook rows remain unchanged.
When the default cleaned JSON already exists, that file is used as the base so
prior cleanups are preserved. The command streams that file to write the updated
complete JSON, but does not semantically review records outside the `Need review`
queue. The `Findings` and `Source Rows` statuses and actions are refreshed for
the selected rows. False-positive mismatch findings are removed. If a queued ID
is already absent from the default cleaned JSON, its `Need review` rows are
marked `Fixed` as already deleted.

To update only `ADDRESS_CONTEXT` findings in the issues workbook, run:

```powershell
nutriqa clean --address-context-only
```

This mode reads only the affected workbook rows and their question/answer text.
It does not open or stream the dataset, call OpenAI or Altibbi, or rewrite the
cleaned JSON. It marks address-context-only findings as `Fixed` and retains the
text when no specific address value is identified or its public status cannot be
confirmed. Rows that also contain other finding types keep their existing
status; the action column records the address finding as fixed and leaves the
other findings alone.

The same workbook-only behavior is available for `ADDRESS_VALUE_POSSIBLE`:

```powershell
nutriqa clean --address-value-possible-only
```

This mode reviews only the matching issue rows and their question/answer text.
It retains a flagged value when its public status cannot be confirmed, marks
that finding `Fixed`, and leaves other finding types unchanged. It does not
read or rewrite the dataset JSON.

### Screen the dataset for personal information

Run a screening pass on the newest screened JSON file in `data`; if none exists,
it uses the newest deduplicated JSON file, then `data/NutriQA-Ar.json`:

```powershell
nutriqa screen --dry-run
```

Screening uses OpenAI GPT-6 Luna with medium reasoning for meaning-based checks.
Local patterns still find email addresses, URLs, phone numbers, and Egyptian ID
numbers. Luna reviews each Question/Answer pair for incomplete fields, whether
the answer matches the question, dates of birth, personal names, and street
addresses. Findings remain reviewable; the command never removes records.
Health details about the questioner and their family are out of scope and are
not reported.

The command sends the original Question and Answer text to OpenAI. A screening
dry run does not change the dataset, but it does make API calls and can incur
charges. Add `OPENAI_API_KEY` to the project `.env` file as described in the
deduplication setup section. Each ordinary, synchronous Responses API call
includes up to ten distinct question-answer pairs, with smaller groups when
text is long. This is one call for several records, not one call per record; the
OpenAI Batch API is not used. By default, the command runs up to ten of these
requests at the same time. Set `--workers` from 1 to 16 to tune parallelism;
for example, `--workers 1` runs requests one at a time. Lower the value if your
OpenAI account reports rate limits. Progress updates as request groups finish,
and successfully completed groups are saved in the resume cache.

```powershell
nutriqa screen --dry-run --workers 10
```

The command writes versioned JSON details and a Markdown summary in `docs`.
Reports include record IDs, fields, model usage, and match types, but never
include matched personal values. Existing reports are preserved. Use
`--limit N` to screen the first N records as a small, chargeable sample, or pass
a JSON path to select an input explicitly:

```powershell
nutriqa screen --dry-run --limit 1000
nutriqa screen data/NutriQA-Ar.json --dry-run
```

Use `--max-cost USD` to stop before starting a request when the conservative
cost estimate would exceed the chosen budget. It counts usage from completed
requests and keeps those results for a later run. Because the estimate uses a
tokenizer and a maximum response size, it is a safety estimate rather than an
exact quote. The report and terminal summary show actual input/output token
usage and estimated API cost for requests made during that run.

Successful semantic results are cached in
`<input-folder>/.nutriqa-screening-cache.sqlite3`. The cache key includes the
question, answer, model, reasoning effort, and screening-rule version, so
unchanged records can reuse results after an interruption or on a later run.
The cache stores category results and validated character offsets, not source
text. OpenAI calls request response storage to be disabled. New prompt/rule
versions or changed text are screened again.

To apply safe span redactions, write a new JSON version and keep every row:

```powershell
nutriqa screen --apply
```

Apply writes a new JSON version, preserves every row, and leaves the source
file unchanged. It redacts detected email addresses and URLs; Egyptian mobile
numbers, numbers with an international dialing prefix, and numbers following a
phone label; Egyptian-ID-shaped values with a valid embedded birth date; name
parts after an explicitly labeled first name; and address values following an
explicit address label. It also redacts recognized numbered street addresses
in Arabic and English. Validated street-address spans found by Luna are also
redacted. The program checks each returned address span against its source
field; if it cannot find the exact text, it adds a review flag and leaves the
text unchanged. `--limit` is only allowed with `--dry-run`.

The report separately flags incomplete questions, incomplete answers,
question-answer mismatches, birth-date disclosures, and personal-name findings
for review; these findings do not change the data. ID and phone redactions are
pattern-based, so inspect the report for possible false positives. Address cues without a
validated span are review flags and remain unchanged. The report's Summary and
Coverage sheets include model, token, cache, and estimated-cost details. These
checks are a review aid, not a complete PII guarantee.

### Export the screening report to Excel

The screening JSON and Markdown files preserve every detection, but the Excel
version is easier to filter and review. Export the newest screening report in
`docs`:

```powershell
nutriqa report --screening
```

The workbook opens on **Source Rows**, which lists each affected dataset row
once and puts the original Question and Answer at the start of the sheet. It
also includes the record ID, other original fields, and the finding categories
for that row. The filterable **Findings** sheet has one row per finding, with
Question and Answer first, followed by the record ID, other original fields,
finding category, count type, confidence, detection detail, and suggested
action. **Summary** contains run totals, category counts, and API usage, while
**Coverage** contains screening notes and rule limits. The Excel workbook can
therefore contain personal or health details from the source data; store and
share it accordingly. The screening JSON and Markdown keep the source text out
of their finding details.

A *finding detail* is one category detected in one field. **Matched values**
counts values found by pattern checks or validated address spans; one finding
detail can contain several matched values. **Review flags** counts fields
flagged by context or quality checks, one flag per field. **Unique rows** counts
each dataset record once.
The Summary reports matched values and review flags separately, and the category
table labels which count type each category uses.
Repeated exports preserve previous workbooks and add a revision suffix. Use a
report path and `--output-dir` to choose a specific input and destination:

```powershell
nutriqa report docs/NutriQA-Ar-v2-deduplicated-screening-report-r7.json --screening -o docs/reports
```

### Export the deduplication review to Excel

The review JSON is intended for the deduplication command, so it is compact and
not convenient to read directly. Export it as two formatted workbooks: one for
exact-match groups and one for similarity candidates:

```powershell
nutriqa report
```

By default, the command reads the newest NutriQA review JSON and writes an exact
report and a similarity report in `docs`. Report filenames carry the same run
version as the review. Repeated report runs keep prior workbooks and add a
revision suffix such as `-r2`. Use `--output-dir`/`-o` to choose a different
folder:

```powershell
nutriqa report data/another-dedup-review.json -o docs/reports
```

The similarity workbook states which questions entered semantic matching and
which exact-match groups were excluded. Each candidate shows its two source
questions, their answers and categories, row IDs, and similarity score. The
matching uses question text only; answers and other fields are included so you
can review the full records. A higher similarity score is a reason to inspect a
pair, not proof that the records are duplicates. Exact-match groups and
similarity candidates are applied automatically unless their `approved` value
is `false`; set it to `false` in JSON to exclude a group or pair. Both reports
label these rows `Auto-apply` or `Excluded`. For either match type, apply keeps
the original question with the most characters; ties keep the earliest row.
Excel reports are for reading and do not write decisions back to JSON.

Review files made before exact-match groups were excluded from semantic
matching use an older format. Generate a fresh review with
`nutriqa deduplicate --dry-run` before running `nutriqa report` or
`nutriqa deduplicate --apply`.

## Duplicate-question production workflow

The command examines the `Question` column in the `Arabic Dataset` sheet across
all categories. It runs strict exact matching first, then searches for similar
meaning only among questions that have no exact-match copies. It does not use
fuzzy wording or character-overlap matching.

![Diagram showing exact-match groups sent to an exact report and excluded from similarity matching, with remaining questions sent to a separate similarity report](docs/deduplication-workflow.svg)

The current sheet has 89,045 question rows. Comparing every possible pair would
produce 3,964,238,880 pairs. Exact groups are removed from the semantic input
entirely; the search uses only questions that occur once in the analyzed rows.
It uses OpenAI's `text-embedding-3-large` model and a USearch approximate
nearest-neighbor index to find likely neighbors instead of calculating every
pair. See the
[OpenAI embeddings guide](https://developers.openai.com/api/docs/guides/embeddings)
and [USearch Python documentation](https://unum-cloud.github.io/USearch/python/index.html).

Add your OpenAI key to a `.env` file in the project directory before a dry run.
Start by copying the provided example, then replace its placeholder:

```powershell
Copy-Item .env.example .env
# Edit .env and set OPENAI_API_KEY=your-api-key
```

The CLI loads `.env` automatically. A value already set in your shell takes
precedence over the `.env` value. The CLI ignores the example values
`your-api-key` and `your-openai-api-key` if one was left in the shell, so the
real value in `.env` can be used.

The command caches embeddings in
`data/.nutriqa-embedding-cache.sqlite3`, so reruns can reuse vectors for
unchanged questions. The cache is local and ignored by Git with the rest of the
`data` folder. Successful embedding batches are saved as they finish. Requests
time out after 60 seconds and retry once; the terminal shows when it is waiting
for OpenAI.

### Resume after an interruption

Rerun the same dry-run command after a network error, crash, or Ctrl+C. Completed
embedding batches are reused from the cache, and semantic-neighbor search saves
a checkpoint every 256 questions. With the same questions, similarity floor,
and `K`, the search resumes from its last checkpoint. Changing the questions,
similarity floor, or `K` starts semantic search again, but cached embeddings
remain available. An API request interrupted before its response is saved may
need to be sent again. Ctrl+C exits after cleanup and reports that saved
progress is available on rerun.

### Candidate-generation steps

1. Assign every source record a stable, one-based row ID and keep its original
   question text.
2. Create a strict exact-match key by normalizing Unicode representation,
   line endings, and whitespace. Do not rewrite Arabic words or reorder text.
   Questions with the same key form an exact-match group. Keep every source row
   ID linked to its group; grouping does not delete rows.
3. Save groups containing repeated questions as exact-match candidates. Exclude
   every row in those groups from semantic matching. Only non-empty questions
   that appear once in the analyzed rows remain in the semantic input.
4. Encode each remaining question in batches with OpenAI
   `text-embedding-3-large`. Check question lengths against the model's input
   limit; do not silently truncate long questions. Cache vectors so reruns do
   not need to encode unchanged text. Successful batches are saved immediately.
5. Put the remaining-question vectors in an approximate nearest-neighbor
   index. For each question, retrieve its top `K` semantic neighbors and keep
   those above a configurable similarity floor. A pair found from either
   direction is retained once. Exact-match groups are not searched or returned
   as semantic candidates.
6. Each dry run writes a new, versioned review JSON. It contains exact groups
   and semantic candidates with questions, source row IDs, scores where
   applicable, and an `approved` field. Its `candidate_rows` section includes
   the original records for review. Run `nutriqa report` to create separate
   exact-match and similarity workbooks. No dataset rows are removed in this
   step.
7. Exact-match groups and similarity candidates are merged automatically unless
   `approved=false`. The apply run writes a new dataset copy and an audit file
   with removed row IDs and the output checksum. Both match types keep the
   original question with the most characters; ties keep the earliest source
   row. Other fields come from that kept record and are not combined.

For scale, retrieving 20 neighbor slots for every original row would return at
most 1,780,900 directed neighbor results before removing self-matches, repeated
pairs, or below-threshold results. The actual semantic search is smaller: it
excludes every exact-match group and searches only questions that occur once.
The command has no default for `K` or the similarity floor. Choose them using a
small `--limit` run and the number of candidate pairs it produces; on apply,
those pairs are merged automatically unless explicitly excluded. The `K=20`
value below is only an example.

In plain language, the exact step puts repeated questions into groups and keeps
the row IDs. Every row in those groups is set aside from similarity matching.
The semantic step checks only questions that appeared once. The model puts each
remaining question at a location on a meaning map, and an index uses shortcuts
to find nearby questions without scoring every possible pair. This search is
approximate, so `K`, the index settings, and the score floor affect which
candidates are considered. On apply, exact groups and similarity pairs are
merged automatically unless excluded with `approved=false`. Connected
similarity candidates can form a larger group through shared rows. In both exact
and similarity groups, the original question with the most characters is kept;
ties keep the earliest row. The full kept record supplies the answer and other
fields. Answers do not affect which candidates are found.

### Run the deduplication command

The input path is optional and defaults to `data/NutriQA-Ar.json`. In an
interactive terminal, missing semantic settings are requested by the CLI:

```powershell
nutriqa deduplicate
```

To try candidate generation on only the first N records, add `--limit`. This
example checks the first 1,000 rows and uses illustrative semantic settings:

```powershell
nutriqa deduplicate --dry-run --limit 1000 --similarity-floor 0.90 --top-k 20
```

The limit applies to exact matching and semantic search, so questions after row
1,000 are not sent for embeddings in this example. The command still scans and
hashes the source file to confirm the review belongs to the same dataset. A
limited review is marked as a sample and cannot be applied to the full dataset.
If the results look good, rerun without `--limit` to prepare the full review.
The embedding cache reuses vectors from the sample, reducing repeated API work.

For a non-interactive run, provide both settings explicitly. These are example
values only, not selected production settings:

```powershell
nutriqa deduplicate --dry-run --similarity-floor 0.90 --top-k 20
```

To run another pass on a deduplicated output, pass that file as the input. This
example uses the previously used `top-k` value and a lower similarity floor:

```powershell
nutriqa deduplicate data/NutriQA-Ar-deduplicated.json --dry-run --similarity-floor 0.80 --top-k 20
nutriqa report
```

The dry run and report use the next shared run version, preserving reports and
datasets from earlier passes. To apply this review to the same input, use:

```powershell
nutriqa deduplicate data/NutriQA-Ar-deduplicated.json --apply
```

Apply exact groups and similarity candidates that are not explicitly excluded:

```powershell
nutriqa deduplicate --apply
```

Each run gets one shared version number for its review, output, and audit
files. The first run uses the base filenames; later runs add a suffix such as
`-v2` before `-dedup-review`, `-deduplicated`, and `-dedup-audit`. `--apply`
uses the newest unapplied review for the input and creates the matching output
and audit filenames. Existing files remain in place. Use `--review`, `--output`,
or `--audit` to choose custom paths; `--force` only replaces an existing file
when an explicit path names it.

Exact-match groups and similarity candidates with `approved=null` or `true` are
applied automatically. Set either candidate type to `approved=false` in the JSON
review file to leave that group or pair separate. For both types, apply keeps
the question with the most characters; ties keep the earliest row. The apply
summary and audit show exact groups and similarity pairs applied as separate
counts.

## JSON format

The output is a UTF-8 JSON object with a format name, format version, sheet
name, ordered column names, and records keyed by those column names. The current
columns come from the workbook header row: `Question`, `Answer`, `Category`,
and `SubCategory`.

```json
{
  "format": "nutriqa-dataset",
  "version": 1,
  "sheet": "Arabic Dataset",
  "columns": ["Question", "Answer", "Category", "SubCategory"],
  "records": [
    {
      "Question": "...",
      "Answer": "...",
      "Category": "...",
      "SubCategory": "..."
    }
  ]
}
```

The conversion keeps row order, headers, cell values, and formula strings. The
generated workbook contains only the `Arabic Dataset` sheet; Excel formatting
and other workbook features are not represented in this JSON format.
