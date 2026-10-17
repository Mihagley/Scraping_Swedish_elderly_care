"""Starta med: python -m streamlit run streamlit_app.py."""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import streamlit as st
import yaml

from municipal_research.config import LLM, Config, load_config
from municipal_research.interface import configure, parse_locations, requires_openai
from municipal_research.llm import check_ollama

ROOT = Path(__file__).resolve().parent
DEMO, LIVE = "Demonstration", "Verklig sökning"
PROVIDERS = {
    "hybrid": "Webbsökning och genomsökning",
    "openai": "Webbsökning med OpenAI",
    "crawl": "Genomsök webbplatser",
}


def show_error(message, error):
    st.error(message)
    with st.expander("Tekniska detaljer"):
        st.code(str(error))


def swedish_sort_key(location):
    return location.name.lower().translate(str.maketrans({"å": "{a", "ä": "{b", "ö": "{c"}))


def launch_job(settings=None):
    """One process and cache per browser session; no API key is saved to disk."""
    session = Path(st.session_state.job_root)
    job = Path(tempfile.mkdtemp(prefix="job-", dir=session))
    command = [sys.executable, "-m", "municipal_research.cli"]
    if settings is None:
        command += ["demo", "--config", str(ROOT / "examples/language_requirements.yaml")]
        summary = "Demonstration med fasta, påhittade kommuner och regler."
        api_key = ""
    else:
        config, locations, api_key, collect_only = settings
        (job / "config.yaml").write_text(
            yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True), encoding="utf-8"
        )
        with (job / "locations.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "id",
                    "name",
                    "kind",
                    "domains",
                    "seeds",
                    "portal_hosts",
                    "public_endpoints",
                ],
            )
            writer.writeheader()
            for location in locations:
                item = location.model_dump()
                item["domains"] = ";".join(item["domains"])
                item["seeds"] = ";".join(item["seeds"])
                item["portal_hosts"] = ";".join(item["portal_hosts"])
                item["public_endpoints"] = ";".join(item["public_endpoints"])
                writer.writerow(item)
        command += [
            "run",
            "--cancel-file",
            str(job / "cancel.requested"),
            "--config",
            str(job / "config.yaml"),
            "--locations",
            str(job / "locations.csv"),
        ]
        if collect_only:
            command.append("--collect-only")
        summary = (
            f"{len(locations)} valda platser · högst {config.discovery.max_documents} kandidathämtningar per plats (inklusive misslyckade) · "
            f"{'enbart insamling' if collect_only else 'klassificering med AI'}\n\n"
            f"Utredningsfråga: {config.research.question}\n\n"
            f"Sökord: {', '.join(config.discovery.keywords)}"
        )
        summary += f"\n\nTak: {config.limits.run_requests} nät-/modellanrop och {config.limits.run_seconds:g} sekunder totalt; {config.limits.organisation_requests} anrop och {config.limits.organisation_seconds:g} sekunder per organisation. AI-tak {config.llm.max_calls} totalt / {config.llm.max_calls_per_organisation} per organisation."
        if not collect_only:
            summary += f"\n\nAI: {config.llm.provider} · {', '.join(config.llm.models)}"
        if config.discovery.provider != "crawl":
            summary += "\n\nWebbsökning med OpenAI ingår i denna körning."
        st.session_state.submitted_locations = [location.name for location in locations]
        st.session_state.saved_settings = (config, locations, collect_only)
    resume_source = st.session_state.get("resume_source") if settings is not None else None
    if resume_source:
        command += ["--resume-from", resume_source]
    command += ["--run-dir", str(job / "run"), "--cache-dir", str(session / "cache")]
    env = dict(os.environ, OPENAI_API_KEY=api_key)
    with (job / "console.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    st.session_state.update(
        process=process,
        job=str(job),
        completion_shown=False,
        was_demo=settings is None,
        stop_requested=False,
        submitted_summary=summary,
    )
    st.rerun()


def live_form(running):
    try:
        locations = parse_locations(
            (ROOT / "examples/municipalities.csv").read_text(encoding="utf-8")
        )
        locations += parse_locations((ROOT / "examples/regions.csv").read_text(encoding="utf-8"))
        base = load_config(ROOT / "examples/interface.yaml")
        with st.sidebar.expander("Egna listor och kodbok"):
            st.caption("CSV ersätter hela platslistan. YAML ersätter kodboken för utredningen.")
            upload = st.file_uploader(
                "Egen lista med kommuner och regioner (CSV)", type=["csv"], disabled=running
            )
            if upload:
                locations = parse_locations(upload.getvalue().decode("utf-8-sig"))
            custom = st.file_uploader("Egen kodbok (YAML)", type=["yaml", "yml"], disabled=running)
            if custom:
                base = Config.model_validate(yaml.safe_load(custom.getvalue()))
        if len({location.id for location in locations}) != len(locations):
            raise ValueError("Alla kommuner och regioner måste ha unika id-värden.")
    except Exception as error:
        show_error("Det gick inte att läsa platslistan eller kodboken. Kontrollera filerna.", error)
        return

    st.subheader("1. Vad vill du ta reda på?")
    question = st.text_area("Utredningsfråga", base.research.question, disabled=running)
    keywords = st.text_area(
        "Sökord och fraser",
        ", ".join(base.discovery.keywords),
        disabled=running,
        help="Separera ord och fraser med kommatecken eller radbrytningar.",
    )
    with st.expander("Termvarianter – valfria och redigerbara"):
        if re.search(r"språk|svenska", question, re.I):
            if st.button("Fyll i exempel för språkfrågor", disabled=running):
                st.session_state.variant_terms = "kunskaper i svenska, svenska i tal och skrift, språktest, god svenska, behärska svenska"
        variants = st.text_area(
            "Egna termvarianter",
            key="variant_terms",
            disabled=running,
            help="Läggs till sökorden för rangordning. Höjer aldrig hämtningsgränsen. Inga språkvarianter läggs till automatiskt.",
        )
        apply_variants = st.checkbox(
            "Använd termvarianterna för denna fråga",
            key="apply_variants_" + str(hash(question)),
            disabled=running,
        )
        if apply_variants:
            keywords = keywords + "," + variants
    if apply_variants and variants.strip():
        st.caption("Aktiva termvarianter för denna fråga: " + variants)
    st.caption(
        "Sökorden hjälper till att hitta relevanta sidor. Utredningsfrågan och kodboken styr hur innehållet bedöms."
    )

    count_kommun = sum(location.kind == "kommun" for location in locations)
    count_region = sum(location.kind == "region" for location in locations)
    st.caption(f"Tillgängligt: {count_kommun} kommuner och {count_region} regioner.")
    if upload is None:
        st.caption(
            "Kommunernas webbplatser och koder är hämtade från SKR:s listor, kontrollerade 15 oktober 2026."
        )
    st.subheader("2. Var vill du söka?")
    scope = st.radio(
        "Var vill du söka?",
        ["Kommuner", "Regioner", "Både kommuner och regioner"],
        horizontal=True,
        disabled=running,
    )
    kinds = {
        "Kommuner": {"kommun"},
        "Regioner": {"region"},
        "Både kommuner och regioner": {"kommun", "region"},
    }
    options = sorted(
        [location for location in locations if location.kind in kinds[scope]], key=swedish_sort_key
    )
    lookup = {location.id: location for location in options}
    selection_context = (scope, tuple(lookup))
    if st.session_state.get("selection_context") != selection_context:
        st.session_state.selection_context = selection_context
        st.session_state.location_ids = list(lookup)[:1]
    selection_mode = st.radio(
        "Vilka platser vill du ta med?",
        ["Välj enskilda", "Alla i listan"],
        horizontal=True,
        disabled=running,
        key="selection_mode",
    )
    if selection_mode == "Alla i listan":
        selected = list(lookup)
        st.success(f"Alla {len(selected)} i listan är valda.")
    else:
        if st.button("Rensa val", disabled=running):
            st.session_state.location_ids = []
        selected = st.multiselect(
            "Sök och välj i listan",
            list(lookup),
            key="location_ids",
            disabled=running,
            format_func=lambda key: f"{lookup[key].name} ({lookup[key].kind})",
            placeholder="Skriv ett namn för att hitta en kommun eller region",
        )
    st.caption(f"Valda: {len(selected)} av {len(options)}.")
    if "region" in kinds[scope]:
        st.caption(
            "Regionsökning söker på regionernas egna webbplatser. Kommunernas webbplatser väljs separat."
        )
    with st.expander("Visa valda webbplatser"):
        st.dataframe(
            [
                {
                    "Namn": lookup[key].name,
                    "Typ": lookup[key].kind,
                    "Webbplatser": ", ".join(lookup[key].domains),
                }
                for key in selected
            ],
            hide_index=True,
        )

    with st.sidebar:
        st.subheader("Analys och sökinställningar")
        ai_provider = st.selectbox(
            "AI för klassificering",
            ["openai", "ollama"],
            format_func=lambda value: (
                "OpenAI (molntjänst)" if value == "openai" else "Lokal AI via Ollama"
            ),
            index=["openai", "ollama"].index(base.llm.provider),
            disabled=running,
            key="ai_provider",
        )
        if ai_provider == "openai" and base.llm.provider == "ollama":
            st.caption(
                "Vid byte från en lokal YAML-konfiguration används OpenAI-exempelmodellen gpt-5-mini. Ladda upp en OpenAI-konfiguration för att välja andra molnmodeller."
            )
        api_key = st.text_input(
            "Din API-nyckel för OpenAI",
            type="password",
            disabled=running,
            key="openai_api_key",
            help="Valfri för genomsökning och lokal AI. Behövs för OpenAI-klassificering och OpenAI-webbsökning. Nyckeln skrivs inte till konfigurationsfiler.",
        ).strip()
        local_model = local_url = None
        context_tokens = local_timeout = None
        if ai_provider == "ollama":
            st.info(
                "Textanalys och verifiering körs med din nedladdade modell i Ollama. Ingen OpenAI-nyckel behövs när sökmetoden är Genomsök webbplatser."
            )
            local_model = st.text_input(
                "Lokal modell (inklusive tagg)",
                value=base.llm.models[0] if base.llm.provider == "ollama" else "llama3.1:8b",
                disabled=running,
            )
            local_url = st.text_input(
                "Ollama-adress på din dator", base.llm.ollama_base_url, disabled=running
            )
            st.caption(
                "Installera och starta Ollama. Hämta en lokal modell med exempelvis ollama pull llama3.1:8b. Välj inte en molnmodell."
            )
            with st.expander("Inställningar för lokal modell"):
                context_tokens = st.number_input(
                    "Kontextfönster (token)",
                    2048,
                    262144,
                    base.llm.ollama_num_ctx,
                    step=1024,
                    disabled=running,
                )
                local_timeout = st.number_input(
                    "Väntetid per modellanrop (sekunder)",
                    30,
                    3600,
                    int(base.llm.timeout_seconds) if base.llm.provider == "ollama" else 600,
                    disabled=running,
                )
                st.caption(
                    "Ett större kontextfönster kräver mer minne. Det måste rymma instruktioner, källtext och modellens svar. Kontrollera resultaten med din valda modell."
                )
            if st.button("Testa anslutning till Ollama", disabled=running):
                try:
                    settings = LLM(
                        provider="ollama",
                        models=[local_model.strip()],
                        verifier_model=local_model.strip(),
                        adjudicator_model=local_model.strip(),
                        ollama_base_url=local_url.strip(),
                    )
                    check_ollama(settings)
                    st.success(
                        "Ollama svarar och modellen finns installerad. Modellsvaren valideras när sökningen körs."
                    )
                except Exception as error:
                    show_error(
                        "Ollama kunde inte kontrolleras. Starta tjänsten och kontrollera modellnamnet.",
                        error,
                    )
        discovery_context = (ai_provider, base.discovery.provider)
        if st.session_state.get("discovery_context") != discovery_context:
            st.session_state.discovery_context = discovery_context
            st.session_state.discovery_provider = (
                "crawl" if ai_provider == "ollama" else base.discovery.provider
            )
        if not api_key:
            st.session_state.discovery_provider = "crawl"
            st.caption(
                "Ingen OpenAI-nyckel angiven: sökmetoden är automatiskt Genomsök webbplatser. Ange en nyckel för att aktivera OpenAI-webbsökning."
            )
        collect_only = st.checkbox("Samla bara in källor, utan klassificering", disabled=running)
        with st.expander("Avancerade sökinställningar"):
            queries = st.text_area(
                "Sökfrågor för OpenAI-webbsökning (en per rad)",
                "\n".join(base.discovery.queries) or "{location} {question}",
                help="Används inte av Genomsök webbplatser. Där rangordnas länkar med sökorden. {location} ersätts med platsens namn, {domain} med domänen och {question} med utredningsfrågan.",
                disabled=running,
            )
            provider = st.selectbox(
                "Sökmetod",
                list(PROVIDERS),
                format_func=PROVIDERS.get,
                index=None,
                disabled=running or not api_key,
                key="discovery_provider",
            )
            max_documents = st.number_input(
                "Högst antal kandidathämtningar per organisation",
                1,
                10000,
                base.discovery.max_documents,
                help="Försök, inte antal lyckade dokument. Navigationssidor och misslyckade kandidathämtningar ingår i samma tak. Robots, sitemaps, omförsök, omdirigeringar och AI-anrop ingår dessutom i de separata anropstaken.",
                disabled=running,
            )
            max_sitemaps = st.number_input(
                "Högst sitemap-filer per organisation",
                0,
                100,
                base.discovery.max_sitemaps,
                disabled=running,
            )
            max_chunks = st.number_input(
                "Högst textdelar per läsbar källa",
                1,
                1000,
                min(1000, base.extraction.max_chunks_per_document),
                disabled=running,
            )
            max_pdf_pages = st.number_input(
                "Högst PDF-sidor per källa",
                1,
                2000,
                base.extraction.max_pdf_pages,
                disabled=running,
            )
            extraction_seconds = st.number_input(
                "Sekunder per text-/länkextraktion",
                1,
                120,
                int(base.limits.extraction_seconds),
                disabled=running,
            )
            st.caption(
                f"HTTP: högst {base.network.retries} omförsök och {base.network.max_redirects} omdirigeringar per URL; högst {base.network.max_bytes} byte per svar. Alla verkliga försök ryms i anropstaken. Inget extra navigationsutrymme läggs till kandidattaket."
            )
            max_calls = st.number_input(
                "Högst antal modellanrop under hela körningen",
                1,
                100000,
                base.llm.max_calls,
                disabled=running,
            )
            use_cutoff = st.checkbox(
                "Avgränsa till regler som gällde före ett visst datum",
                value=base.research.cutoff is not None,
                disabled=running,
            )
            cutoff = (
                st.date_input(
                    "Gränsdatum (själva datumet ingår inte)",
                    value=base.research.cutoff or date.today(),
                    format="YYYY-MM-DD",
                    disabled=running,
                )
                if use_cutoff
                else None
            )
            st.caption(
                "För att ändra bedömningskategorier eller instruktioner, ladda upp en egen kodbok i sidopanelen. Att ändra sökord skriver inte om kodboken."
            )
            st.write("**Instruktioner i aktuell kodbok**")
            st.write(base.research.instructions)
            st.table(
                [
                    {"Kategori": label, "Definition": rule.definition}
                    for label, rule in base.research.labels.items()
                ]
            )
    with st.sidebar.expander("Hårda budgetgränser", expanded=True):
        st.caption(
            "Inget tak höjs automatiskt. Alla faktiska HTTP-/modellanrop räknas, inklusive robots, sitemaps, omdirigeringar och HTTP-omförsök. AI-anrop görs utan dolda SDK-omförsök."
        )
        run_requests = st.number_input(
            "Anrop totalt", 1, 100000, base.limits.run_requests, disabled=running
        )
        org_requests = st.number_input(
            "Anrop per organisation", 1, 10000, base.limits.organisation_requests, disabled=running
        )
        run_seconds = st.number_input(
            "Sekunder totalt", 1, 86400, int(base.limits.run_seconds), disabled=running
        )
        org_seconds = st.number_input(
            "Sekunder per organisation",
            1,
            3600,
            int(base.limits.organisation_seconds),
            disabled=running,
        )
        org_calls = st.number_input(
            "AI-anrop per organisation",
            1,
            100000,
            base.llm.max_calls_per_organisation,
            disabled=running,
        )
    with st.sidebar.expander("AI-arbetsmängd och granskning"):
        analysis_mode = st.selectbox(
            "Analysläge",
            ["verified", "light"],
            format_func=lambda value: (
                "Flera pass + semantisk verifiering"
                if value == "verified"
                else "Lätt: ett pass + citatkontroll, kräver granskning"
            ),
            index=["verified", "light"].index(base.llm.analysis_mode),
            disabled=running,
        )
        passes = st.number_input(
            "Klassificeringspass i flerpassetsläget",
            1,
            10,
            base.llm.passes,
            disabled=running or analysis_mode == "light",
        )
        adjudicate = st.checkbox(
            "Tillåt högst en skiljebedömning + verifiering per textdel",
            value=base.llm.adjudicate,
            disabled=running or analysis_mode == "light",
        )
        st.caption(
            "Samma modell vid temperatur noll ger inte oberoende bekräftelse. Lättläget behåller mekanisk citatkontroll men saknar separat semantisk verifiering och måste granskas av en människa. Likvärdig träffsäkerhet är inte utvärderad."
        )
        input_tokens = st.number_input(
            "Högsta indata (konservativ tokenövregräns)",
            256,
            200000,
            base.llm.input_tokens,
            disabled=running,
        )
        st.caption(
            "UTF-8-byteantal inklusive schema används som försiktig övregräns. För stora frågor/historiker avvisas, inte tyst trunkeras. Ollamas kontext måste även rymma svaret."
        )
    per_chunk = 0 if collect_only else 1 if analysis_mode == "light" else int(passes) + 1
    upper_per_chunk = per_chunk + (
        2 if adjudicate and analysis_mode == "verified" and not collect_only else 0
    )
    scenario = len(selected) * int(max_documents) * per_chunk
    st.caption(
        f"Planeringsscenario: om varje kandidathämtning ger en textdel behövs {scenario} analysanrop ({per_chunk} per textdel). Verklig mängd beror på läsbarhet och textlängd; i flerpassetsläget kan högst {upper_per_chunk} anrop behövas per textdel. Webbsökning kan tillkomma men ryms i samma AI-tak."
    )
    st.caption(
        f"Teknisk övregräns för analys före budgetstopp: {len(selected) * int(max_documents) * int(max_chunks) * upper_per_chunk} anrop, om alla kandidater gav {max_chunks} textdelar. Det valda AI-taket gäller alltid först."
    )
    st.caption(
        f"Hårda tak: {max_documents} kandidathämtningar/organisation; {run_requests} anrop och {run_seconds} sekunder totalt; {org_requests} anrop och {org_seconds} sekunder/organisation. AI: {max_calls} totalt, högst {org_calls}/organisation. Oanvänd budget flyttas inte automatiskt till en ny körning."
    )
    st.caption(
        f"Organisationer behandlas i listordning. Första organisationen kan använda upp till {min(int(org_calls), int(max_calls))} AI-anrop; budgeten delas inte automatiskt lika. Sänk organisationstaket för att reservera fler anrop till senare organisationer."
    )
    st.caption(
        "AI-analysen gör lokala Ollama-anrop."
        if ai_provider == "ollama"
        else "OpenAI-analysen använder betalda anrop."
    )
    if scenario > max_calls and not collect_only:
        st.warning(
            "AI-taket räcker inte till planeringsscenariot. Delresultat sparas när taket nås. Minska urval/pass, välj lättläge eller ange ett nytt uttryckligt tak."
        )
    st.caption(
        "Webbläsarrendering och OCR körs inte automatiskt. JavaScript-sidor och skannade PDF-sidor sparas och flaggas som luckor. Officiella portalvärdar och offentliga dataadresser kan anges uttryckligen i CSV (portal_hosts och public_endpoints)."
    )
    needs_key = requires_openai(provider, ai_provider, collect_only)
    st.divider()
    st.subheader("3. Granska och starta")
    if needs_key and not api_key:
        st.warning(
            "Genomsökning behöver ingen nyckel, men klassificering med OpenAI gör det. Ange en API-nyckel, välj Lokal AI via Ollama eller markera Samla bara in källor i sidopanelen."
        )
    elif needs_key:
        st.caption("Sökningen använder betalda API-anrop. Antalsgränsen är ingen kostnadsgräns.")
    else:
        st.caption("Denna kombination kräver ingen OpenAI-nyckel.")
    if ai_provider == "ollama" and provider != "crawl":
        st.warning(
            "Webbsökningen skickar sökfrågor till OpenAI och kräver en API-nyckel. Textklassificeringen använder fortfarande Ollama. Välj Genomsök webbplatser för att undvika OpenAI helt."
        )
    summary_columns = st.columns(3)
    summary_columns[0].metric("Valda organisationer", len(selected))
    summary_columns[1].metric("Hämtningsförsök per organisation", max_documents)
    summary_columns[2].metric(
        "Analys",
        "Enbart insamling" if collect_only else "Ollama" if ai_provider == "ollama" else "OpenAI",
    )
    if len(selected) >= 100:
        st.warning(
            "Du har valt en stor sökning. Den kan ta lång tid. Börja gärna med några kommuner för att kontrollera frågan och resultaten."
        )
    if st.button(
        "Starta sökning",
        use_container_width=True,
        disabled=running or not selected or (needs_key and not api_key),
        type="primary",
    ):
        try:
            if needs_key and not api_key.strip():
                st.error(
                    "Ange din API-nyckel, eller välj Genomsök webbplatser tillsammans med lokal AI eller enbart insamling."
                )
                return
            config = configure(
                base,
                question,
                keywords,
                queries,
                provider,
                int(max_documents),
                int(max_calls),
                cutoff,
                ai_provider=ai_provider,
                local_model=local_model,
                local_url=local_url,
                context_tokens=context_tokens,
                local_timeout=local_timeout,
            )
            settings = config.model_dump(mode="json")
            settings["limits"].update(
                run_requests=int(run_requests),
                organisation_requests=int(org_requests),
                run_seconds=int(run_seconds),
                organisation_seconds=int(org_seconds),
                extraction_seconds=int(extraction_seconds),
            )
            settings["extraction"].update(
                max_pdf_pages=int(max_pdf_pages), max_chunks_per_document=int(max_chunks)
            )
            settings["discovery"]["max_sitemaps"] = int(max_sitemaps)
            settings["llm"].update(
                analysis_mode=analysis_mode,
                passes=int(passes),
                adjudicate=adjudicate,
                max_calls_per_organisation=int(org_calls),
                input_tokens=int(input_tokens),
            )
            config = Config.model_validate(settings)
            st.session_state.resume_source = None
            launch_job((config, [lookup[key] for key in selected], api_key.strip(), collect_only))
        except Exception as error:
            show_error(
                "Sökningen kunde inte startas. Kontrollera frågan och inställningarna.", error
            )


@st.fragment(run_every="2s")
def show_job():
    process = st.session_state.get("process")
    if process is None:
        return
    job = Path(st.session_state.job)
    code = process.poll()
    if code is not None and not st.session_state.get("completion_shown", False):
        st.session_state.completion_shown = True
        st.rerun()
    st.subheader("Resultat och aktivitet")
    budget_path = job / "run/budget.json"
    if budget_path.exists():
        budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
        used = budget_data.get("used", {})
        st.caption(
            f"Lokala modellanrop: {used.get('local_model_requests', 0)} · betalda modellanrop: {used.get('paid_model_requests', 0)} · AI-anrop kvar: {budget_data.get('remaining_model_calls', 0)}."
        )
        st.caption(
            f"Anrop: {used.get('requests', 0)} / {budget_data['limits']['run_requests']}; kvar {budget_data['remaining_requests']}. Senast registrerat tidsutrymme: {budget_data['remaining_seconds']} s. Organisation: {budget_data['organisation_id']}, {budget_data['organisation_remaining_requests']} anrop kvar."
        )
        st.caption(
            f"Kandidathämtningar {used.get('attempted_retrievals', 0)} · misslyckade {used.get('failed_downloads', 0)} · navigationssidor {used.get('navigation_pages', 0)} · läsbara underlag {used.get('usable_documents', 0)}. Navigation och läsbara underlag kan överlappa; läsbarhet är inte ett bevis för ett fynd."
        )
    st.write(st.session_state.submitted_summary)
    if not st.session_state.was_demo:
        with st.expander("Platser som ingår i körningen"):
            st.write(", ".join(st.session_state.submitted_locations))
    if code is None:
        st.info("Sökningen pågår. Senaste aktivitet visas i loggen nedan.")
        if st.button("Avbryt sökning", disabled=st.session_state.stop_requested):
            try:
                (job / "cancel.requested").touch()
            except OSError:
                pass  # Process may have finished between polling and the click.
            st.session_state.stop_requested = True
            st.warning("Avbrott begärt. Körningen avslutas.")
    elif st.session_state.stop_requested:
        st.warning(
            "Sökningen avbröts. Resultaten kan vara ofullständiga. Starta en ny körning för att försöka igen."
        )
    elif code in (0, 2):
        if st.session_state.was_demo:
            st.warning("Demonstrationen är klar. Kommunerna och fynden i resultaten är påhittade.")
        elif code == 2:
            st.warning(
                "Klar med luckor eller fynd som behöver granskas. Kontrollera flikarna Summary och Errors i Excel-filen."
            )
        else:
            st.success("Sökningen är klar.")
        for filename, label, mime in [
            (
                "results.xlsx",
                "Ladda ner Excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ("results.json", "Ladda ner fullständiga data (JSON)", "application/json"),
        ]:
            path = job / "run" / filename
            if path.exists():
                st.download_button(label, path.read_bytes(), file_name=filename, mime=mime)
        results = job / "run/results.json"
        if results.exists():
            try:
                data = json.loads(results.read_text(encoding="utf-8"))
                st.dataframe(
                    [
                        {
                            "Plats": row["municipality"],
                            "Bedömning": row["category"],
                            "Lästa källor": row["documents"],
                            "Försök": row.get("counts", {}).get("attempted_retrievals", 0),
                            "Navigationssidor": row.get("counts", {}).get("navigation_pages", 0),
                            "Misslyckade": row.get("counts", {}).get("failed_downloads", 0),
                            "Läsbara underlag": row.get("counts", {}).get("usable_documents", 0),
                            "Behöver granskas": "Ja" if row["needs_review"] else "Nej",
                        }
                        for row in data["summary"]
                    ],
                    hide_index=True,
                )
            except (ValueError, KeyError):
                st.caption(
                    "Förhandsvisningen kunde inte läsas. Ladda ner filerna för att granska resultatet."
                )
    else:
        st.error("Körningen misslyckades. Se den tekniska loggen för detaljer.")
    progress_path = job / "run/results.json"
    if code is not None and progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8")).get("progress", {})
        translations = {
            "candidate_attempt_limit": "taket för kandidathämtningar nått",
            "finalisation_limit": "slutexport avbruten eller hoppad över vid tidsgräns/avbrott",
            "request_limit": "anropstaket nått",
            "model_call_limit": "AI-taket nått",
            "elapsed_time": "tidsgränsen nådd",
            "cancelled": "avbruten av användaren",
        }
        for stop in progress.get("stopping_reasons", []):
            st.warning(
                f"{stop['municipality_id']}: {translations.get(stop['reason'], stop['reason'])} ({'hela körningen' if stop['scope'] == 'run' else 'organisationen'}). Delresultat har sparats."
            )
        st.caption(
            "Inte påbörjade organisationer: "
            + (", ".join(progress.get("unprocessed_organisations", [])) or "inga")
        )
        st.caption(
            "Ofullständiga organisationer: "
            + (", ".join(progress.get("incomplete_organisations", [])) or "inga")
        )
        st.caption(
            "Uteblivet fynd betyder inte att ett krav saknas; sökningen är avgränsad av de valda taken."
        )
        if not st.session_state.was_demo and (
            progress.get("incomplete_organisations") or progress.get("unprocessed_organisations")
        ):
            st.info(
                "Fortsättning är ett uttryckligt nytt budgetbeslut. Tidigare resultat lämnas orörda; nästa körning får samma tak som den sparade körningen och arbetar från dess kontrollpunkter. För andra tak: använd CLI --resume-from med ny YAML."
            )
            if st.button("Fortsätt i ny körning med samma uttryckliga tak"):
                saved_config, saved_locations, saved_collect = st.session_state.saved_settings
                key = st.session_state.get("openai_api_key", "").strip()
                if (
                    requires_openai(
                        saved_config.discovery.provider, saved_config.llm.provider, saved_collect
                    )
                    and not key
                ):
                    st.error("Ange API-nyckeln igen innan du fortsätter.")
                else:
                    st.session_state.resume_source = str(job / "run")
                    launch_job((saved_config, saved_locations, key, saved_collect))
    with st.expander("Teknisk logg", expanded=code not in (0, 2)):
        parts = []
        for path, limit in [(job / "run/audit.jsonl", 12000), (job / "console.log", 4000)]:
            if path.exists():
                parts.append(path.read_text(encoding="utf-8", errors="replace")[-limit:])
        st.code("\n".join(parts) or "Ingen aktivitet har registrerats ännu.")


st.set_page_config(page_title="Utredningsstödet", page_icon="🔎", layout="wide")
st.markdown(
    """
    <style>
    .block-container {max-width: 1040px; padding-top: 2.5rem; padding-bottom: 4rem;}
    h1 {letter-spacing: -0.045em; font-weight: 750;}
    h2, h3 {letter-spacing: -0.025em;}
    [data-testid="stSidebar"] {border-right: 1px solid #dce5e2;}
    [data-testid="stTextArea"] textarea {line-height: 1.6;}
    [data-testid="stButton"] button {border-radius: 0.65rem; min-height: 2.7rem;}
    [data-testid="stDownloadButton"] button {border-radius: 0.65rem;}
    </style>
    """,
    unsafe_allow_html=True,
)
st.caption("OFFENTLIGA KÄLLOR · SPÅRBARA RESULTAT")
st.title("Utredningsstödet")
st.caption(
    "Hitta uppgifter på kommuners och regioners webbplatser, spara källorna och ladda ner resultaten."
)
if "job_root" not in st.session_state:
    local_data = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
        / "Utredningsstodet"
        / "korningar"
    )
    local_data.mkdir(parents=True, exist_ok=True)
    st.session_state.job_root = tempfile.mkdtemp(prefix="session-", dir=local_data)
st.sidebar.caption("Körningsfiler sparas lokalt i: " + st.session_state.job_root)
active = st.session_state.get("process")
running = active is not None and active.poll() is None
st.sidebar.title("Inställningar")
st.sidebar.caption("Välj AI och anpassa sökningen här. Din fråga och dina val finns i huvudvyn.")
mode = st.radio("Välj körläge", [DEMO, LIVE], horizontal=True, disabled=running)
show_job()
if mode == DEMO:
    st.info(
        "Prova arbetsflödet utan internet eller API-nyckel. Demonstrationen använder fasta, påhittade kommuner och regler. Den söker inte på riktiga webbplatser."
    )
    if st.button("Starta demonstration", disabled=running, type="primary"):
        try:
            launch_job()
        except Exception as error:
            show_error("Demonstrationen kunde inte startas.", error)
else:
    live_form(running)
