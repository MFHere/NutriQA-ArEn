# NutriQA-ArEn v1 reproducibility record

This document records evidence available for the v1 release. It distinguishes research-team confirmations from settings demonstrated by historical logs. It is not a claim of a complete production run log.

## Dataset

- Final dataset: 75,113 bilingual QA records.
- Zenodo record: https://zenodo.org/records/24086067
- Local final JSON SHA-256: `9986f280298fd2e2b9ae0675fecc5febfafa3214f3419eb3e36e27dff4687cd9`.
- Local final Excel SHA-256: `f28d1dd8550372c09788fc5fd0cc9e5fbb4163c1e49cff08b534c300493587f3`.

Checksums refer to the local release files inspected for this record; the deposited download has not been independently compared with these local files.

## Code and production settings

- This documentation update was prepared against repository commit `268e22971a9d8b504c3c9a14f7b3a98967caca3b`. That identifies the reviewed code base; it does not establish the code revision used by every historical run. Record the new release commit after these changes are committed.
- Book OCR: Mistral OCR (`mistral-ocr-latest`); book QA: `gpt-5-mini-2025-08-07`, as confirmed by the research team and reflected in the book workflow defaults.
- Arabic-to-English translation: `gpt-3.5-turbo`, as confirmed by the research team. The available `TranslationToEnglish/translation.log` records an unsuccessful run and does not independently establish successful batch coverage.
- Historical deduplication: 442 exact and 6,593 semantic removals in a documented 89,045-record run; semantic matching used cosine similarity threshold 0.90 and `text-embedding-3-large` embeddings.
- Historical screening: 82,010 records. Counts in historical logs are not evidence that every record in the reconstructed 89,311-record starting cohort received the same logged processing.
- The research team confirms that 266 records were included in the original dataset before cleaning; their individual coverage is not established by the available historical reports.

## Dependencies and execution

Each component should be installed in its own Python environment using its component instructions and dependency files. `requirements.lock.txt` files in `FromAlTibbi.com/`, `FormYoutube/`, `FromArabicDietBooks/` and `TranslationToEnglish/` capture currently available local Python 3.12.10 component environments; they document those environments, not necessarily the environments used during dataset production. `DataNormalization/requirements.lock.txt` captures the installed dependency closure of the current system Python 3.12.10 environment. Its command-line help was smoke-tested, but no original normalization environment or complete production run has been verified. This snapshot does not include the `hatchling` build backend. The YouTube snapshot omits Streamlit and `python-multipart`, which are needed for the optional interface and upload API and require separate installation and verification. Component requirements or lockfiles describe available setups for future use; they should not be treated as recovered production environments without independent evidence.

The project does not yet have a complete linked manifest of all historical input hashes, exact commands, run dates, model snapshots, logs and per-record cleaning decisions. Preserve and add those artifacts as they become available. Some inputs and logs may require access controls. API keys must not be published. Model outputs and upstream sources may change over time, so reruns may not be bit-for-bit identical.
