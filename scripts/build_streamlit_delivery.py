"""Build a reviewable Streamlit source ZIP and Swedish PDF guides, without network work."""

import base64
import json
import zipfile
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

SECTIONS = [
    (
        "Utredningsstödet – avgränsad sökning",
        "Packa upp hela ZIP-filen. Windows: starta start-interface.bat. macOS/Linux: kör sh start-interface.sh. Python 3.11 eller senare behövs vid installation. Behåll terminalen öppen. Prova Demonstration innan en verklig sökning. Gränssnitt och skrapare körs på din dator.",
    ),
    (
        "Fem försök, även när inget hittas",
        "Standard är 5 kandidathämtningsförsök per organisation. Välj gärna 4–5 för en smal fråga. Navigationssidor och misslyckade kandidathämtningar ingår i samma tak. Inget extra navigationsutrymme läggs till. Det äldre YAML-namnet discovery.max_documents betyder försök, inte lyckade dokument. Dina valda värden gäller; inget tak höjs automatiskt.",
    ),
    (
        "Hårda budgetar",
        "Grundinställning: 500 faktiska HTTP-/modellanrop och 1 800 sekunder totalt; 40 anrop och 180 sekunder per organisation. Ändra uttryckligen i sidopanelen. Robots, sitemaps, HTTP-omförsök, omdirigeringar och modellbegäranden använder samma anropsbudget. Ett kandidatförsök kan kräva flera sådana anrop. En cacheträff använder ett kandidatförsök men inget nytt nätanrop. OpenAI-webbverktygets interna arbete hos leverantören är inte klientens HTTP-anrop.",
    ),
    (
        "Även långsamma operationer har slut",
        "Produktionens DNS/HTTP, modellbegäranden och text-/länkextraktion körs i ägda processer som kan avslutas vid deadline. HTTP: normalt 30 sekunder per begäran, högst 3 omförsök, 5 omdirigeringar och 25 MB per svar. Allt kläms till återstående budget. Dolda SDK-omförsök för modeller är avstängda. Textutvinning: normalt högst 20 sekunder och 100 PDF-sidor per källa. Sitemap-, textdels- och PDF-tak visas i avancerade inställningar. Slutlig lokal Excel-export och kontrollsummering tillkommer. En timeout garanterar inte att en redan accepterad leverantörsbegäran är kostnadsfri.",
    ),
    (
        "Tydliga räknare och delresultat",
        "Försök, misslyckade hämtningar, navigationssidor och läsbara underlag redovisas separat. Navigation identifieras heuristiskt och kan överlappa läsbara underlag. Läsbarhet är inte ett belagt fynd. Stopporsak, använda/kvarvarande anrop, tidsutrymme, ofullständiga och ej påbörjade organisationer redovisas. JSON, Excel, råkällor och kontrollpunkter bevaras. Inget fynd inom dessa gränser betyder aldrig bevis för frånvaro.",
    ),
    (
        "Relevans inom samma lilla budget",
        "Frön, startsidor, sitemaps och vanlig HTTP finns kvar. URL, titel, ankartext och dina termer styr rangordningen. Opaka sitemap/PDF-adresser filtreras inte bort enbart för att URL:en saknar sökord. Loggen sparar metod, föräldersida och matchande termer. Termvarianter är redigerbara. För språkfrågor kan du själv fylla i kunskaper i svenska, svenska i tal och skrift, språktest, god svenska och behärska svenska. Ingen sådan lista läggs automatiskt på andra frågor. Sökfrågefältet gäller OpenAI-/hybridwebbsökning; genomsökning använder sökorden för rangordning.",
    ),
    (
        "Officiella portaler och offentliga data",
        "En egen CSV kan innehålla portal_hosts (godkända värdnamn) och public_endpoints (offentliga HTTP(S)-adresser på godkända domäner). Separera värden med semikolon. Kontrollera själv portalens officiella koppling innan den läggs till. Externa länkar betros inte automatiskt. Offentliga endpoints används som frön inom samma kandidatgräns; JSON och dess tillåtna länkar kan läsas. Ingen automatisk privat API-upptäckt, inloggning eller obegränsad paginering görs.",
    ),
    (
        "Oläsbara källor är luckor",
        "Misstänkta JavaScript-skal, felsidor och skannade/sparsamma PDF-sidor sparas och flaggas. Webbläsarrendering och OCR ingår inte som automatisk reservväg i denna version. Inga sådana jobb körs i bakgrunden. Detekteringen är heuristisk; granska råkällorna eller konfigurera en verifierad offentlig dataadress. Blockerade eller oläsbara dokument får inte tolkas som negativa sakfynd.",
    ),
    (
        "AI-arbetsmängd och lättläge",
        "Streamlit behåller normalt 50 modellanrop totalt och 20 per organisation. Organisationer behandlas i listordning; budgeten delas inte automatiskt lika. Första organisationen kan använda det lägsta av organisationstaket och totaltaket. Sänk organisationstaket om du vill lämna fler anrop till senare organisationer. Lokala Ollama-anrop skiljs från betalda OpenAI-anrop. Insamling utan AI fungerar fortfarande.",
    ),
    (
        "Flera pass är inte oberoende bekräftelse",
        "Flerpassläget har konfigurerbart antal pass, normalt 3, plus semantisk verifiering: normalt 4 anrop per textdel. Om tillåtet kan högst en skiljebedömning med verifiering tillkomma; med 3 pass är övre gränsen 6 anrop per textdel. Lättläget gör ett pass och mekanisk citat-/fältkontroll; det markeras alltid för mänsklig granskning. Det har ingen separat semantisk verifiering och likvärdig träffsäkerhet påstås inte. Samma modell vid temperatur noll ger inte oberoende bekräftelse.",
    ),
    (
        "Beviskontroller och kontext",
        "Exakta/blankteckennormaliserade citat, sid-/radpositioner, källhashar, promptar/svar, avståenden och granskningsflaggor finns kvar. UTF-8-byteantal inklusive instruktioner och schema används som konservativ tokenövregräns. Ollamas svar och en marginal måste också få plats i kontexten. För stora underlag eller skiljehistoriker avvisas och flaggas; de trunkeras inte tyst. Den försiktiga bytegränsen kan avvisa text som en exakt tokenizer annars skulle få plats med.",
    ),
    (
        "Avbryt och fortsätt uttryckligen",
        "Avbryt sökning skriver en avbrottsbegäran som kontrolleras även under ägda operationer. Delresultat och kö sparas. Fortsätt i ny körning med samma uttryckliga tak är ett nytt synligt budgetbeslut och startas aldrig automatiskt. Tidigare run ändras inte. Nya räknare gäller den nya fortsättningen, medan källor/klassificeringar är kumulativa. Flera uttryckligen godkända körningar kan tillsammans göra fler försök än en ensam körning.",
    ),
    (
        "CLI för fortsättning",
        "För andra nya budgetar: municipal-research run --config nya-budgetar.yaml --locations organisationer.csv --run-dir runs/fortsatt-01 --cache-dir data/cache --resume-from runs/avbruten-01 . Behåll samma fråga, kodbok, analysmetod och organisationslista samt originalets cache. Lägg till --collect-only om originalet enbart samlade källor. Källhashar kontrolleras; en pågående redan hämtad källa återspelas från cache. Streamlit visar run-sökvägen i sidopanelen; nya sessioner sparas beständigt under LOCALAPPDATA/Utredningsstodet/korningar på Windows.",
    ),
    (
        "Offline replay och färska källor är skilda val",
        "--offline skapar en separat körning utan HTTP-/modellnätverk; cachemiss är ett fel. --refresh skapar en annan ny körning och använder ETag/Last-Modified när de finns. HTTP 304 återanvänder verifierade råbytes; ändrat material sparas som ny representation. Dessa lägen kan inte kombineras med --resume-from. En vanlig körning kan återanvända cache utan aktualitetskontroll; välj refresh för en uttrycklig aktualitetskontroll.",
    ),
    (
        "Liten jämförelse på samma sparade källor",
        "python -m municipal_research.evaluation prepare runs/pilot eval/packet.json --limit 5 . Högst 20, normalt 10, sparade textdelar väljs från en förseglad körning. Paketet innehåller källor och kodbok men inga tidigare modellförslag eller mänskliga referensetiketter. En separat blank referensmall är inget facit.",
    ),
    (
        "Regler, Llama och OpenAI",
        "Kör exempelvis python -m municipal_research.evaluation run eval/packet.json eval/rules --engine rules --max-calls 1 --seconds 60 . Regelbaslinjen avstår om ingen uttrycklig bokstavlig regel har valts med --rule-pattern och --rule-label. Lokal jämförelse använder --engine ollama --model llama3.1:8b ; betald jämförelse använder --engine openai --model DIN_MODELL . Ange alltid --max-calls och --seconds och välj nya outputmappar. Modeller måste finnas installerade/tillgängliga. Inga sådana livejämförelser startas som rutinmässigt implementationstest.",
    ),
    (
        "Oberoende referenser före resultat",
        "Två olika personer ska annotera respektive kontrollera etiketter från källorna med proveniens, blinda för modell-/regelsuggestions. Referensfilen behöver annotator, checked_by, blind_to_suggestions och proveniens per textdel. En modells egna svar får inte bli dess facit. Programmet kräver attesteringar men kan inte verifiera faktisk blinding.",
    ),
    (
        "Mått och vad som fortfarande saknas",
        "Score-kommandot tar packet.json, predictions.json, en separat referensfil och en ny rapportfil. Precision/recall per klass hålls skilda från dokument-recall. Dokument-recall är null utan en oberoende mänsklig lista relevant_urls och gäller bara den referensmängden, aldrig hela webben. Datum/scope, avståenden, ej behandlade textdelar, tid och lokala/betalda anrop redovisas. Semantiskt obelagda påståenden kräver separat mänsklig prediktionsgranskning; utan den är måttet null, inte noll. Mekaniska citatfel visas separat.",
    ),
    (
        "Valideringsgränser",
        "Automatiska regressioner använder sparade/syntetiska källor och mockade transporter. De är inte ett mänskligt validerat facit för svenska kommuner. Ingen rikstäckande insamling eller betald/obegränsad modellbenchmark har startats. Verkliga portaladresser, lokala modellvarianter och småbudgets-recall/precision behöver separat liten livepilot och blind mänsklig granskning. OCR och webbläsaråterhämtning återstår.",
    ),
    (
        "Primära tekniska referenser",
        "Scrapy dynamic content: https://docs.scrapy.org/en/latest/topics/dynamic-content.html ; beständiga jobb: https://docs.scrapy.org/en/latest/topics/jobs.html ; pypdf textutvinning: https://pypdf.readthedocs.io/en/stable/user/extract-text.html ; Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs ; MDN conditional requests: https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Conditional_requests ; ACL 2025 human annotation: https://aclanthology.org/2025.findings-acl.1323/ .",
    ),
]


def build():
    root = Path(__file__).resolve().parents[1]
    fontdir = Path("/usr/share/fonts/truetype/dejavu")
    regular, bold = "Helvetica", "Helvetica-Bold"
    if (fontdir / "DejaVuSans.ttf").exists():
        pdfmetrics.registerFont(TTFont("Guide", str(fontdir / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("GuideBold", str(fontdir / "DejaVuSans-Bold.ttf")))
        regular, bold = "Guide", "GuideBold"
    body = ParagraphStyle("body", fontName=regular, fontSize=9.5, leading=14, spaceAfter=9)
    heading = ParagraphStyle(
        "heading",
        parent=body,
        fontName=bold,
        fontSize=14,
        leading=19,
        spaceBefore=12,
        keepWithNext=True,
        textColor=colors.HexColor("#176B59"),
    )
    flow = []
    for title, text in SECTIONS:
        flow.extend(
            [Paragraph(escape(title), heading), Paragraph(escape(text), body), Spacer(1, 3)]
        )
    guide = root / "docs/budgetar-och-utvardering.pdf"
    SimpleDocTemplate(
        str(guide),
        pagesize=A4,
        leftMargin=45,
        rightMargin=45,
        topMargin=40,
        bottomMargin=40,
        title="Utredningsstödet – budgetar och utvärdering",
    ).build(flow)
    for target in (root / "LAS_MIG.pdf", root / "docs/interface.pdf"):
        target.write_bytes(guide.read_bytes())
    print("DELIVERY_PDF_BASE64:" + base64.b64encode(guide.read_bytes()).decode())
    destination = root / "delivery"
    destination.mkdir(exist_ok=True)
    archive = destination / "Utredningsstodet-Streamlit-budgetar.zip"
    excluded = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "delivery",
        "data",
        "runs",
        "build",
        "dist",
        "research",
        ".mypy_cache",
    }
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or any(part in excluded for part in relative.parts):
                continue
            zipped.write(path, "Utredningsstodet/" + str(relative))
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(destination)
        assert "Utredningsstodet/.streamlit/config.toml" in zipped.namelist()
        assert "Utredningsstodet/docs/budgetar-och-utvardering.pdf" in zipped.namelist()
        assert (
            "max_documents"
            in zipped.read("Utredningsstodet/src/municipal_research/config.py").decode()
        )
    print(json.dumps({"zip": archive.name, "bytes": archive.stat().st_size}))


if __name__ == "__main__":
    build()
