# Sample reports

Four public end-of-well report JSONs from block 25/6 and 25/7, for trying the pipeline
without the full dataset.

| Wellbore | File | Pages |
|---|---|---|
| 25/6-1 | `reports/25_6-1/WELL/25_6-1_25-06-01_PB-706-0464.JSON` | 143 |
| 25/6-3 | `reports/25_6-3/WELL/25_6-3_25-06-03_PB-706-0221.JSON` | 104 |
| 25/7-2 | `reports/25_7-2/WELL/25_7-2_25-07-02_PB-706-0941.JSON` | 265 |
| 25/7-3 | `reports/25_7-3/WELL/25_7-3_25-07-03_PB-706-0732.JSON` | 220 |

The folder layout matches the source archive: `<wellbore>/WELL/<name>.JSON`.

## What is in a file

One document per file: `original_document_metadata` (wellbore name, title, dates, page
quality summary) and `pages[]`. Each page has OCR text as markdown (`OCR.ocr_markdown`),
layout boxes, a quality label, and machine-written tags, a page summary and figure
descriptions under `AI`.

## What is not here

The matching PDFs (14 to 155 MB each, 242 MB together) are not in the repo: one exceeds
GitHub's 100 MB file limit. Get them from the source record below.

## Source and licence

Zenodo record 24320092, DOI [10.5281/zenodo.24320092](https://doi.org/10.5281/zenodo.24320092):
"4462 public wellbore completion reports in pdf and json format from the Norwegian
continental shelf". Licence CC BY 4.0. Attribution: ConocoPhillips / Diskos.
