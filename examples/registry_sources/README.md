# Kommunregister – källor och kontroll

`examples/municipalities.csv` innehåller Sveriges 290 kommuner med fyrsiffriga
kommunkoder (inledande nollor bevaras) och officiella webbplatser.

Underlaget hämtades 2026-10-15 från Sveriges Kommuner och Regioner (SKR):

- Webbplatser: https://skr.se/kommunerochregioner/kommunerlista.8288.html
- Kommunkoder: https://skr.se/kommunerochregioner/kommunkoder.8282.html

JSON-filerna här sparar endast de metadata som användes: namn, värdnamn respektive
kommunkod och länsbokstav, tillsammans med källadress och hämtningsdatum. De sparar
inte sidornas övriga innehåll. Vid import matchas de två listorna på kommunnamn.
Den enda namnnormaliseringen är `Malung – Sälen` → `Malung-Sälen`.

Återskapa CSV-filen utan internet:

```sh
python scripts/build_municipality_registry.py
```

Importen kräver exakt 290 unika namn på båda sidor, fullständig namnmatchning och
unika koder. Tester jämför den levererade CSV-filen med importresultatet och
kontrollerar att de tidigare sex kommunerna har kvar sina koder.

Domäner hämtas från SKR:s länkade värdnamn, utan inledande `www.` så att både
huvuddomän och underdomäner tillåts. Startsidorna använder HTTPS. Stockholms tidigare
angivna portal `start.stockholm` finns också kvar. Inga domäner har gissats utifrån
kommunnamn. `examples/municipalities_pilot.csv` bevarar den tidigare sexkommunlistan.

Detta kontrollerar registret mot SKR, inte varje webbplats drifttillgänglighet,
robotsregler, externa dokumentarkiv eller täckning av historiska dokument. En körning
kan därför rapportera luckor även när kommunen finns med i registret.

Regionregistret är separat. Region Gotland förekommer även som kommun med en annan
kod och typ; samma organisation kan utföra båda uppgifterna. Regionsökning söker på
regionens egen webbplats, inte på samtliga kommuners webbplatser i regionen.
