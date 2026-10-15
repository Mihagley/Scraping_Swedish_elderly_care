# Sök i offentliga källor

Gränssnittet är på svenska. Du kan välja bland **alla 290 kommuner och 21 regioner**.
Kommunernas webbplatser och koder är matchade mot SKR:s register (2026-10-15).

## Starta på din dator

Du behöver Python 3.11 eller senare och internet för installationen.

1. Packa upp hela ZIP-filen till en egen mapp.
2. Windows: dubbelklicka på `start-interface.bat`.
   macOS/Linux: öppna en terminal i mappen och kör `sh start-interface.sh`.
3. Öppna http://localhost:8501 om webbläsaren inte öppnas automatiskt.
4. Låt terminalfönstret vara öppet medan du använder appen. Avsluta med Ctrl+C.

Appen kör den kopia av skraparen som finns i den uppackade mappen. Den ansluter
inte automatiskt till en annan installation på datorn. Visa webbläsarfönstret för
kollegorna, exempelvis genom skärmdelning. Ingen webbpublicering behövs.

## Prova eller sök på riktigt

- **Demonstration** använder fasta påhittade exempel, utan API-nyckel eller nätanrop.
- **Verklig sökning** använder dina valda kommuner/regioner, fråga och sökord.

I verklig sökning väljer du Kommuner, Regioner eller båda. Använd **Alla i listan** för
hela listan, eller skriv namn och välj enskilda platser. **Rensa val** tömmer urvalet.
Regionsökning söker regionens egna webbplatser; välj kommunerna separat för att
undersöka kommunala verksamheter.

Ange forskningsfråga och sökord. Under avancerade inställningar kan du ändra sökmetod,
dokumentgräns, högst antal modellanrop, historiskt gränsdatum och enbart insamling.
Kodboken styr hur innehållet bedöms; för en annan typ av forskningsfråga kan du ladda
upp en egen YAML-kodbok i sidopanelen. Sökord används för att prioritera källor.

Webbsökning och AI-klassificering kräver din OpenAI API-nyckel och kostar pengar.
**Genomsök webbplatser** tillsammans med **Samla bara in källor** kräver ingen nyckel.
Börja gärna med ett litet urval innan du söker i hela landet.

Starta sökningen. Status och det inskickade urvalet visas ovanför formuläret.
När körningen är klar kan du ladda ner Excel och fullständiga data i JSON.
Excel-flikarnas tekniska namn är på engelska för kompatibilitet med befintliga körningar.
Ett granskningsbehov eller en lucka betyder inte att kommunen saknar en viss regel.

Hämta resultaten innan du stänger appen. Tillfälliga filer är inte en säker långtidslagring.
Mer teknisk information finns i `docs/interface.md` och källor i
`examples/registry_sources/README.md`.
