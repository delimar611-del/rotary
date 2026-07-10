# Formati ulaznih datoteka

Sve datoteke su JSON, kodiranje **UTF-8** (č ž š ć đ moraju ostati netaknuti),
datumi **DD.MM.YYYY**, iznosi broj s decimalnom **točkom** (1234.56, ne 1.234,56).

## Varijanta A — gotov matched.json (izlaz vaše match_payments.py skripte)

Lista objekata, jedan po računu:

```json
[
  {
    "inv": "512/P1/1",
    "buyer": "Trgovina Čavlović d.o.o.",
    "buyer_oib": "12345678901",
    "date": "20.06.2026",
    "amount": 1845.50,
    "pay_status": "UNPAID",
    "paid_sum": 0.0,
    "payment_refs": []
  }
]
```

`pay_status` smije biti samo: `PAID`, `PAID (platform)`, `PARTIAL`, `UNPAID`, `N/A (storno)`.

Uvoz: `py -m receivables ingest matched.json`

## Varijanta B — sirovi podaci u dvije liste (za punjenje jeftinijim modelom)

Model NE odlučuje što je plaćeno — samo prepisuje. Sparivanje radi aplikacija.

**invoices.json** — svi izlazni računi:

```json
[
  {"inv": "512/P1/1", "buyer": "Trgovina Čavlović d.o.o.", "buyer_oib": "12345678901",
   "date": "20.06.2026", "amount": 1845.50, "storno": false}
]
```

**payments.json** — sve uplate s izvoda:

```json
[
  {"date": "02.07.2026", "payer": "TRGOVINA ČAVLOVIĆ D.O.O.", "payer_oib": "12345678901",
   "amount": 1845.50, "ref": "opis/poziv na broj ako postoji, inače prazno"}
]
```

`payer_oib` upišite ako postoji na izvodu; ako ne postoji, ostavite `""` —
sparivanje će pokušati po nazivu uplatitelja (velika/mala slova, kvačice i
d.o.o./obrt dodaci ne smetaju).

Uvoz u jednom koraku (FIFO sparivanje je ugrađeno u aplikaciju):

```
py -m receivables match invoices.json payments.json
```

Usput zapiše i `matched.json` + `unmatched.json` pored ulaznih datoteka,
pa uvijek možete vidjeti što je s čime spojeno.

## Gotov prompt za jeftiniji model (kopirajte i priložite sirove podatke)

```
Pretvaraš sirove poslovne podatke u JSON. Pravila:
1. Izlaz je ISKLJUČIVO validan JSON, bez komentara i bez markdowna.
2. Kodiranje UTF-8 — hrvatska slova (č ž š ć đ) prepiši točno kako jesu.
3. Datumi u formatu DD.MM.YYYY. Iznosi kao broj s decimalnom točkom (1845.50).
4. NIŠTA ne izmišljaj i NIŠTA ne računaj: ne zbrajaj, ne procjenjuj je li
   nešto plaćeno, ne spajaj uplate s računima. Samo prepiši svaki redak.
5. Ako neki podatak ne postoji u izvoru, stavi prazan string "" (ili false).
6. Na kraju provjeri: broj elemenata u JSON-u mora biti jednak broju redaka
   u izvoru. Ako nisi siguran za neki redak, svejedno ga uključi i dodaj polje
   "check": "PROVJERI RUČNO".

Zadatak: iz priloženih podataka napravi <invoices.json | payments.json>
prema ovom predlošku:
<zalijepi odgovarajući predložak odozgo>
```

Nakon što model vrati JSON: usporedite ukupan broj zapisa i ukupan iznos s
izvornim podacima prije uvoza — model je pomoćnik za tipkanje, ne knjigovođa.
