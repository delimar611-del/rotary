# Pipeline — eposlovanje-naplata skripte

Ovdje dolaze tri postojeće skripte (odložite ih u ovu mapu, logika se NE mijenja):

- `parse_invoices.py` — parsira izlazne račune skinute s ePoslovanja
- `match_payments.py` — FIFO sparivanje uplata s računima → `matched.json`
- `build_xlsx.py` — (opcionalno; dashboard ga zamjenjuje, ali može ostati)

`run_pipeline.py` ih ulančava i na kraju poziva `python -m receivables ingest`.
Jedina prilagodba pri integraciji: hardkodirane putanje u skriptama postaju
argumenti (dogovorit ćemo točan oblik kad skripte budu ovdje).

Scraping ePoslovanja i slanje e-maila ostaju RUČNI koraci — aplikacija to
nikada ne radi sama.
