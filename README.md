# NutriQA-ArEn

NutriQA-ArEn is an Arabic-English nutrition question-answer dataset and its construction pipeline. The current v1 corpus contains 75,113 aligned records from Altibbi consultations, YouTube transcripts, and Arabic nutrition books. The dataset supports research and education and is not a source of medical or dietary advice.

## Pipeline and code

The four-phase workflow combines source-specific acquisition and QA generation with shared normalization and deduplication, translation, and quality screening and cleaning:

1. **Source collection and QA construction**: [Altibbi](FromAlTibbi.com/README.md), [YouTube](FormYoutube/README.md), and [book OCR and QA generation](FromArabicDietBooks/README.md). The book workflow uses Mistral OCR (`mistral-ocr-latest`) and the `gpt-5-mini-2025-08-07` chat model by default.
2. **Normalization and deduplication**: see [DataNormalization](DataNormalization/README.md).
3. **Arabic-to-English translation**: see [TranslationToEnglish](TranslationToEnglish/README.md). The research team reports that `gpt-3.5-turbo` was used for the final corpus.
4. **Screening and cleaning**: see [DataNormalization](DataNormalization/README.md) for commands and screening methods.

The standalone `Fig2 AltibbiYoutubeBooks.svg` reflects an earlier pipeline layout. See Figure 2 in the manuscript for the current four-phase workflow.

Install and run each component in a separate Python environment using its own README and dependency files. Dependency snapshots describe the currently inspected component environments, not necessarily the historical production environments. The components also require external APIs and source access. Example commands in a README are not necessarily the historical production commands. Protect API keys and any source-derived sensitive data.

## Reproducibility and release

[REPRODUCIBILITY.md](REPRODUCIBILITY.md) documents the available historical evidence, confirmed production settings, final dataset checksums, and remaining gaps. External model services and source changes may prevent identical reruns.

The v1 dataset is deposited at [Zenodo record 24086067](https://zenodo.org/records/24086067). During peer review the Excel dataset is intended for editors and reviewers through a confidential link supplied separately. The authors plan public availability at the same record after acceptance or publication. Refer to the record for the applicable release and license information.

## Citation

Cite the associated NutriQA-ArEn manuscript and the version-specific Zenodo dataset record when they are available.
