"""Launch from the repository: python -m streamlit run streamlit_app.py."""
from __future__ import annotations

import csv
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
st.set_page_config(page_title='Swedish public-source research', page_icon='🔎', layout='wide')
st.title('Swedish public-source research')
st.caption('Search kommun and region websites, preserve sources, and export evidence to Excel.')
mode = st.radio('Run mode', ['Demo — invented offline examples', 'Live research'], horizontal=True)
st.info('Kommun includes the six supplied starter locations. Regions includes all 21 regional authorities, including Gotland. Upload a CSV to extend or replace these lists.')

try:
    locations = parse_locations((ROOT / 'examples/municipalities.csv').read_text(encoding='utf-8'))
    locations += parse_locations((ROOT / 'examples/regions.csv').read_text(encoding='utf-8'))
    upload = st.file_uploader('Optional location list (CSV)', type=['csv'])
    if upload:
        locations = parse_locations(upload.getvalue().decode('utf-8-sig'))
    base = load_config(ROOT / 'examples/interface.yaml')
    custom = st.file_uploader('Optional research codebook (YAML)', type=['yaml', 'yml'])
    if custom:
        base = Config.model_validate(yaml.safe_load(custom.getvalue()))
except Exception as error:
    st.error(str(error))
    st.stop()

scope = st.radio('Where should we search?', ['Kommun', 'Region', 'Selected locations'], horizontal=True)
options = [x for x in locations if scope == 'Selected locations' or x.kind == scope.lower()]
lookup = {x.id: x for x in options}
selected = st.multiselect('Locations', list(lookup), default=list(lookup)[:1],
    format_func=lambda key: f'{lookup[key].name} ({lookup[key].kind})')
question = st.text_area('Research question', base.research.question)
keywords = st.text_area('Words or phrases to look for (comma separated)', ', '.join(base.discovery.keywords))
queries = st.text_area('Search queries (one per line)', '\n'.join(base.discovery.queries) or '{location} {question}',
    help='Use {location}, {domain}, or {question} as placeholders.')
with st.expander('Research settings'):
    st.caption('Keywords rank discovery candidates. The research question and codebook determine classification. Upload a YAML codebook to change labels, instructions or extracted fields.')
    st.json(base.research.model_dump(mode='json'))
    provider = st.selectbox('Discovery method', ['hybrid', 'openai', 'crawl'],
        index=['hybrid', 'openai', 'crawl'].index(base.discovery.provider))
    max_documents = st.number_input('Maximum documents per location', 1, 10000, base.discovery.max_documents)
    max_calls = st.number_input('Maximum model calls per run', 1, 100000, base.llm.max_calls)
    use_cutoff = st.checkbox('Use an exclusive historical cutoff', value=base.research.cutoff is not None)
    cutoff = st.date_input('Only policies before this date', value=base.research.cutoff or date.today()) if use_cutoff else None
    collect_only = st.checkbox('Collect sources without classification')
    st.caption('Crawl + collect only needs no API key. Hybrid/OpenAI discovery and classification use paid API calls. The call limit is not a monetary cap.')
api_key = st.text_input('Your OpenAI API key (live runs only)', type='password', help='Passed to your job in memory; never written to configuration files.') if mode == 'Live research' else ''

if 'job_root' not in st.session_state:
    st.session_state.job_root = tempfile.mkdtemp(prefix='municipal-ui-')
active = st.session_state.get('process')
running = active is not None and active.poll() is None
if st.button('Start research' if mode == 'Live research' else 'Run offline demo', disabled=running, type='primary'):
    try:
        if mode == 'Live research' and not selected:
            raise ValueError('Select at least one location.')
        if mode == 'Live research' and (provider != 'crawl' or not collect_only) and not api_key:
            raise ValueError('Enter your API key, or choose crawl with collect only.')
        session = Path(st.session_state.job_root)
        job = Path(tempfile.mkdtemp(prefix='job-', dir=session))
        run = job / 'run'
        command = [sys.executable, '-m', 'municipal_research.cli']
        if mode.startswith('Demo'):
            command += ['demo', '--config', str(ROOT / 'examples/language_requirements.yaml')]
        else:
            config = configure(base, question, keywords, queries, provider, int(max_documents), int(max_calls), cutoff)
            (job / 'config.yaml').write_text(yaml.safe_dump(config.model_dump(mode='json'), allow_unicode=True), encoding='utf-8')
            with (job / 'locations.csv').open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=['id', 'name', 'kind', 'domains', 'seeds'])
                writer.writeheader()
                for key in selected:
                    item = lookup[key].model_dump()
                    item['domains'] = ';'.join(item['domains'])
                    item['seeds'] = ';'.join(item['seeds'])
                    writer.writerow(item)
            command += ['run', '--config', str(job / 'config.yaml'), '--locations', str(job / 'locations.csv')]
            if collect_only:
                command.append('--collect-only')
        command += ['--run-dir', str(run), '--cache-dir', str(session / 'cache')]
        env = dict(os.environ, OPENAI_API_KEY=api_key)
        with (job / 'console.log').open('w') as log:
            st.session_state.process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        st.session_state.job = str(job)
        st.session_state.completion_shown = False
        st.session_state.was_demo = mode.startswith('Demo')
        st.rerun()
    except Exception as error:
        st.error(str(error))

@st.fragment(run_every='2s')
def show_job():
    process = st.session_state.get('process')
    if process is None:
        return
    job = Path(st.session_state.job)
    code = process.poll()
    if code is not None and not st.session_state.get("completion_shown", False):
        st.session_state.completion_shown = True
        st.rerun()
    if code is None:
        st.info('Research is running. You can view the latest activity below.')
        if st.button('Stop this run'):
            process.terminate()
            st.warning('Stop requested. Partial files may remain; start a new run to retry.')
    elif code in (0, 2):
        if st.session_state.was_demo:
            st.warning('Demo completed — all municipalities and findings are invented.')
        elif code == 2:
            st.warning('Completed with gaps or findings requiring review. Check the Errors and Summary sheets.')
        else:
            st.success('Research completed.')
        for filename, mime in [('results.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'), ('results.json', 'application/json')]:
            path = job / 'run' / filename
            if path.exists():
                st.download_button(f'Download {filename}', path.read_bytes(), file_name=filename, mime=mime)
    else:
        st.error('Run stopped or failed. Check the activity below.')
    log = job / 'run' / 'audit.jsonl'
    console = job / 'console.log'
    with st.expander('Latest activity', expanded=code not in (0, 2)):
        st.code((log.read_text(encoding='utf-8', errors='replace')[-12000:] if log.exists() else '') + '\n' + (console.read_text(encoding='utf-8', errors='replace')[-4000:] if console.exists() else ''))

show_job()
