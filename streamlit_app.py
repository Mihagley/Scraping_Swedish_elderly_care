"""Starta med: python -m streamlit run streamlit_app.py."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import streamlit as st
import yaml

from municipal_research.config import Config, load_config
from municipal_research.interface import configure, parse_locations

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
            writer = csv.DictWriter(handle, fieldnames=["id", "name", "kind", "domains", "seeds"])
            writer.writeheader()
            for location in locations:
                item = location.model_dump()
                item["domains"] = ";".join(item["domains"])
                item["seeds"] = ";".join(item["seeds"])
                writer.writerow(item)
        command += [
            "run",
            "--config",
            str(job / "config.yaml"),
            "--locations",
            str(job / "locations.csv"),
        ]
        if collect_only:
            command.append("--collect-only")
        summary = (
            f"{len(locations)} valda platser · högst {config.discovery.max_documents} dokument per plats · "
            f"{'enbart insamling' if collect_only else 'klassificering med AI'}\n\n"
            f"Forskningsfråga: {config.research.question}\n\n"
            f"Sökord: {', '.join(config.discovery.keywords)}"
        )
        st.session_state.submitted_locations = [location.name for location in locations]
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
            st.caption("CSV ersätter hela platslistan. YAML ersätter kodboken för forskningen.")
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

    count_kommun = sum(location.kind == "kommun" for location in locations)
    count_region = sum(location.kind == "region" for location in locations)
    st.caption(f"Tillgängligt: {count_kommun} kommuner och {count_region} regioner.")
    if upload is None:
        st.caption(
            "Kommunernas webbplatser och koder är hämtade från SKR:s listor, kontrollerade 15 oktober 2026."
        )
    st.subheader("1. Välj kommuner eller regioner")
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
    st.info(
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

    st.subheader("2. Beskriv vad du vill hitta")
    question = st.text_area("Forskningsfråga", base.research.question, disabled=running)
    keywords = st.text_area(
        "Sökord och fraser",
        ", ".join(base.discovery.keywords),
        disabled=running,
        help="Separera ord och fraser med kommatecken eller radbrytningar.",
    )
    st.caption(
        "Sökorden hjälper till att hitta relevanta sidor. Forskningsfrågan och kodboken styr hur innehållet bedöms."
    )
    with st.expander("Avancerade sökinställningar"):
        queries = st.text_area(
            "Sökfrågor (en per rad)",
            "\n".join(base.discovery.queries) or "{location} {question}",
            help="{location} ersätts med platsens namn, {domain} med domänen och {question} med forskningsfrågan.",
            disabled=running,
        )
        provider = st.selectbox(
            "Sökmetod",
            list(PROVIDERS),
            format_func=PROVIDERS.get,
            index=list(PROVIDERS).index(base.discovery.provider),
            disabled=running,
        )
        max_documents = st.number_input(
            "Högst antal dokument per plats",
            1,
            10000,
            base.discovery.max_documents,
            disabled=running,
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
        collect_only = st.checkbox("Samla bara in källor, utan klassificering", disabled=running)
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
    needs_key = provider != "crawl" or not collect_only
    st.subheader("3. Starta sökningen")
    if needs_key:
        api_key = st.text_input(
            "Din API-nyckel för OpenAI",
            type="password",
            disabled=running,
            help="Nyckeln skickas till din lokala körning och skrivs inte till konfigurationsfiler.",
        )
        st.caption("Sökningen använder betalda API-anrop. Antalsgränsen är ingen kostnadsgräns.")
    else:
        api_key = ""
        st.caption("Enbart genomsökning med insamling kräver ingen API-nyckel.")
    st.write(
        f"**{len(selected)} valda platser** · högst {max_documents} dokument per plats · "
        + ("enbart insamling" if collect_only else "klassificering med AI")
    )
    if len(selected) >= 100:
        st.warning(
            "Du har valt en stor sökning. Den kan ta lång tid. Börja gärna med några kommuner för att kontrollera frågan och resultaten."
        )
    if st.button("Starta sökning", disabled=running or not selected, type="primary"):
        try:
            if needs_key and not api_key.strip():
                st.error(
                    "Ange din API-nyckel, eller välj Genomsök webbplatser och Samla bara in källor."
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
            )
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
    st.subheader("Aktuell körning")
    st.write(st.session_state.submitted_summary)
    if not st.session_state.was_demo:
        with st.expander("Platser som ingår i körningen"):
            st.write(", ".join(st.session_state.submitted_locations))
    if code is None:
        st.info("Sökningen pågår. Senaste aktivitet visas i loggen nedan.")
        if st.button("Avbryt sökning", disabled=st.session_state.stop_requested):
            try:
                process.terminate()
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
                            "Dokument": row["documents"],
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
    with st.expander("Teknisk logg", expanded=code not in (0, 2)):
        parts = []
        for path, limit in [(job / "run/audit.jsonl", 12000), (job / "console.log", 4000)]:
            if path.exists():
                parts.append(path.read_text(encoding="utf-8", errors="replace")[-limit:])
        st.code("\n".join(parts) or "Ingen aktivitet har registrerats ännu.")


st.set_page_config(page_title="Sök i offentliga källor", page_icon="🔎", layout="wide")
st.title("Sök i offentliga källor")
st.caption(
    "Hitta uppgifter på kommuners och regioners webbplatser, spara källorna och ladda ner resultaten."
)
if "job_root" not in st.session_state:
    st.session_state.job_root = tempfile.mkdtemp(prefix="municipal-ui-")
active = st.session_state.get("process")
running = active is not None and active.poll() is None
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
