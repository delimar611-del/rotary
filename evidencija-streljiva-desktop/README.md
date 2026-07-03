# Evidencija streljiva — desktop verzija (Windows)

Desktop inačica aplikacije za evidenciju prodanog streljiva. Ista aplikacija
kao web-verzija u mapi `evidencija-streljiva/`, ali se instalira na računalo
i **podatke sprema u datoteku na disku** umjesto u preglednik:

- podaci: `Dokumenti\Evidencija streljiva\evidencija.json`
- automatska dnevna sigurnosna kopija: `Dokumenti\Evidencija streljiva\kopije\`
  (čuva se zadnjih 30 dana)

Brisanje povijesti ili kolačića preglednika NE utječe na podatke ove verzije.

## Kako preuzeti instalaciju

Instalacijski `.exe` automatski se gradi na GitHubu (kartica **Actions**,
workflow "Build desktop app"). Otvorite zadnje uspješno pokretanje, pri dnu
pod **Artifacts** preuzmite `Evidencija-streljiva-instalacija`, raspakirajte
ZIP i pokrenite `.exe`.

## Prijenos podataka iz web-verzije

1. U web-verziji (preglednik) kliknite **🗄️ Sigurnosna kopija** — preuzima se `.json` datoteka.
2. U desktop aplikaciji kliknite **↩️ Vrati iz kopije** i odaberite tu datoteku.

## Razvoj

```
npm install
npm start        # pokretanje u razvoju
npm run dist     # izgradnja Windows instalacije (dist/*.exe)
```
