# Scholar Data Baseline

This baseline records the local inputs available for Scholar P0. Per-file byte sizes and UTC modification times, plus the sampled field shapes, are in [`services/wisepen-scholar-service/docs/data-baseline.json`](data-baseline.json).

## Inputs

| Input | Repository-relative path | Observed size / shape |
|---|---|---|
| arXiv metadata | `services/wisepen-scholar-service/third_party_data/arxiv-metadata-oai-snapshot.json` | 5,649,388,235 bytes; JSONL |
| S2 papers | `s2_download/papers/` | 60 gzip JSONL shards; 53,124,464,221 compressed bytes |
| S2 TLDRs | `s2_download/tldrs/` | 30 gzip JSONL shards; 6,582,078,516 compressed bytes |
| Single-paper API experiment | `s2_full_fields.json` | JSON object; 50,658 bytes |
| Batch API experiment | `s2_batch_results.json` | JSON array with 3 records; 113,884 bytes |

S2 release `2026-10-20` is the value declared by `RELEASE_ID` in `services/wisepen-scholar-service/scripts/download_s2.py`. This is provenance from the local downloader; it was not independently confirmed against the official service during this baseline capture.

## Sampled Fields

The recorded schema sample read only the first arXiv JSONL record and the first decompressed JSONL record from each of the 90 S2 shards. The JSON experiment files were parsed for object/array shape and field names only; no record values or credentials are copied into the baseline. No large-file hash, complete gzip validation, or full-corpus import was performed. A sample that parses does not establish that the rest of a file or corpus is complete.

The arXiv sample contained `id`, `doi`, `title`, `abstract`, `authors`, `categories`, `versions`, and `update_date`, among other source fields. `versions` is an array of objects containing `version` and `created`. Initialization uses `id` as the arXiv identity, `doi` as an optional exact identifier, and the selected title, abstract, author, category, version, and update-date facts. `created` on the first version supplies the initial submission date; `update_date` retains its source meaning.

The sampled S2 `papers` records contained `corpusid`, `externalids`, `citationcount`, `influentialcitationcount`, and `isopenaccess`, as well as other fields. Offline enrichment selects `corpusid`, exact `externalids.ArXiv` / `externalids.DOI` links, and those three signal fields. The sampled `tldrs` records contained `corpusid`, `model`, and `text`; initialization selects the configured `tldr@v2.0.0` model and keeps the text as supplied, including an empty text value.

The saved S2 API experiments are an object for the single-paper response and an array of three records for the batch response. Their observed field names and nested response shapes are recorded in the JSON manifest. The selected API contract remains the plan's identity fields (`paperId`, `corpusId`, `externalIds`, `url`, `title`) and enrichment fields (`tldr`, `citationCount`, `influentialCitationCount`, `isOpenAccess`, `openAccessPdf.url`, `citationStyles.bibtex`), with `abstract`, `authors`, `year`, `publicationDate`, and `venue` requested only when full details are missing. Fields present in saved responses do not become part of the selected contract by virtue of being present.

## Credentials And Exclusions

The S2 downloader reads `S2_API_KEY` from the process environment or `services/wisepen-scholar-service/.env`. The local env file is not included in this document or the JSON manifest. S2 API requests omit the default `User-Agent`; proxy selection and TLS verification use requests' normal environment and secure defaults.

Git and Docker ignore the arXiv source directory, S2 download directory, saved paper-response experiments, Scholar runtime data, logs, and DuckDB files. Input data is not moved or deleted.
