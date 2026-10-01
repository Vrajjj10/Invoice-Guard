# Demo invoices

Upload in this order (the duplicate needs #1 processed first). All fictional.

| File | Expected decision |
|---|---|
| `1_clean.pdf` | approve (posted to mock SAP) |
| `2_clean_cgst_sgst.pdf` | approve |
| `3_wrong_total.pdf` | manual_review (grand total overstated by 500.00) |
| `4_duplicate_of_1.pdf` | reject (same vendor/number/total as #1, different layout) |
| `5_not_invoice.pdf` | reject (bank-detail change letter) |

`sample_*.{pdf,png}` come from `scripts/make_sample.py`.

```
curl -F "file=@data/samples/1_clean.pdf" -H "X-API-Key: $DEMO_API_KEY" https://<app>.onrender.com/invoices/upload
```
