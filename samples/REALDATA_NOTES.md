# Real-data samples — provenance & assumptions

`samples/realistic_gl.csv` (60 rows) and `samples/realistic_lhdn.json`
(53 docs) are built by `samples/build_realdata.py` from **real public
transaction data**. Regenerate any time with:

```bash
python samples/build_realdata.py --count 60 --offset 5000 --seed 7
```

## What is genuinely real

- **Source:** UCI Machine Learning Repository, *"Online Retail II"*
  (Daqing Chen, 2012) — 1,061,371 real transactions from a UK online
  gift-ware retailer, 01/12/2009–09/12/2011. CC-BY-4.0.
  DOI: https://doi.org/10.24432/C5CG6D
- **Invoice numbers, dates and line amounts** in these samples are real
  invoices from that dataset (20,951 clean invoices aggregated; window of
  60 taken at offset 5000). Cancellations, zero/negative quantities and
  prices were excluded.
- **LHDN field shape is authentic:** the JSON mirrors the real MyInvois
  `DocumentDetails` structure documented at
  `sdk.myinvois.hasil.gov.my` — `uuid`, `submissionUid`, `internalId`,
  `issuerTin`, `issuerName`, `dateTimeIssued`, `totalExcludingTax`,
  `totalPayableAmount`, `status`. Our parser accepts these natively
  (`issuerTin`, `totalPayableAmount`, `internalId`, `dateTimeIssued`;
  SST is derived as payable − excluding).

## Documented assumptions (what is NOT real)

1. **Single supplier TIN** (`C98765432019`, imputed demo value) — the retail
   dataset has customers, not supplier tax IDs; all rows belong to one
   retailer, so one TIN is correct in shape.
2. **SST imputed at 6%** — UK gift-ware carried VAT, not Malaysian SST.
   `subtotal = total / 1.06`, `sst = total − subtotal`. Amounts stay in
   original GBP magnitudes, labelled RM for engine purposes.
3. **Submission gaps are simulated** (seeded): ~1-in-7 invoices dropped
   (`Unsubmitted_Sales`), 8%-rate SST on some rows (`SST_Rate_Mismatch`),
   `-R`-suffixed resubmissions one day later (`Missing_UUID`), one
   LHDN-only document, UUIDs as deterministic UUIDv5 values.
4. Dates shifted to ISO calendar dates; times normalised to 10:00 UTC.

Expected reconciliation on the default window:
**Matched=44, Unsubmitted=8, SST_Rate_Mismatch=5, Missing_UUID=4**
(pinned by `test_matcher_realistic_files_roundtrip`).
