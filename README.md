# keikkalista

Päivittäin päivittyvä Helsingin seudun keikkalista. Scraper hakee tapahtumat 18 keikkapaikalta joka aamu, ja staattinen sivu näyttää ne yhdessä listassa.

- **Lista:** `docs/index.html` (julkaise GitHub Pagesilla `docs/`-kansiosta) lukee `docs/events.json`:ää
- **Scraper:** `python3 scrape.py --out docs/events.json`
- **Aikataulu:** `.github/workflows/update-events.yml` ajaa joka päivä klo 04:00 UTC ja commitoi päivityksen

## Paikat ja lähteet (30.9.2026: 654 keikkaa, 16/18 automaattisesti)

| Paikka | Menetelmä | Tila |
|---|---|---|
| Kulttuuritalo | HTML-listaus (`/tapahtuma/`) | ok |
| Savoy-teatteri | Helsingin Linked Events API (`tprek:7258`) | ok |
| Kuudes Linja | HTML-listaus (`article.event`) | ok |
| Ääniwalli | HTML-listaus (`.event-wrapper`) | ok |
| G Livelab Helsinki | HTML-listaus (`ul.listing`) | ok |
| Tavastia | HTML-listaus (`/events/YYYY-MM-DD/…`) | ok |
| Semifinal | sama, `/semifinal`-sivu | ok |
| Korjaamo | HTML-listaus (`.gt-event-style-1`) | ok |
| Lepakkomies | HTML-listaus (`article`) | ok |
| On The Rocks | HTML-listaus (`article`) | ok |
| Helsingin Jäähalli | listaus + tapahtumasivut | ok |
| Bar Loose | JSON-LD (`schema.org/Event`) | ok |
| Olarin Panimo Helsinki | HTML-listaus (päivämäärä+otsikko-parit) | ok |
| Tiivistämö | Helsingin Linked Events API (`tprek:8099`) | ok |
| Musiikkitalo | kalenterin upotettu JSON (`eventCalendarParams`) | ok |
| Vernissa (Tikkurila) | Stadissan tapahtumapaikkasivu (oma kalenteri rikki) | rajallinen* |
| Veikkaus Arena | – | **manuaalinen**: JS-renderöity Live Nation -sivu; sivu linkkaa viralliseen kalenteriin |
| Allas Live | – | **manuaalinen**: JS-renderöity Live Nation -sivu; sivu linkkaa viralliseen kalenteriin |

Rikkoutunut yksittäinen lähde ei kaada ajoa: virhe kirjataan `events.json`-tiedoston `venues[id].status`-kenttään ja muut paikat päivittyvät normaalisti.

\* Vernissa: kaupungin oma kalenterisivu (`tapahtumat.vantaa.fi/vernissa`) näyttää virhettä, joten lähteenä on Stadissa, jonka kattavuus riippuu käyttäjien ilmoituksista.

## Kehitys

```bash
pip install -r requirements.txt
python3 scrape.py --only tavastia,semifinal --out /tmp/test.json
```

`venues.json` määrittelee paikat; `scrape.py`:n `PARSERS`-sanakirja valitsee parserin `method`-kentän perusteella.

Parannusideoita: Veikkaus Arena + Allas Live headless-selaimella (Playwright) tai Ticketmaster Discovery API -avaimella (`TICKETMASTER_API_KEY`); Kulttuuritalolle kellonajat tapahtumasivuilta.
