import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Gara a Tappe - Live", page_icon="🏁", layout="wide")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PART_HEADER = ["pettorale", "nome"]
TAPPE_HEADER = ["ordine", "tappa"]
ARRIVI_HEADER = ["pettorale", "nome", "tappa", "timestamp"]

CACHE_TTL = 30  # secondi: bilancia freschezza dati e quota API


# ---------- CONNESSIONE GOOGLE SHEETS ----------
@st.cache_resource
def get_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


@st.cache_resource
def get_spreadsheet():
    client = get_client()
    return client.open_by_key(st.secrets["spreadsheet_id"])


@st.cache_resource
def get_worksheet(name, header_tuple):
    ss = get_spreadsheet()
    header = list(header_tuple)
    try:
        ws = ss.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=1000, cols=max(10, len(header)))
        ws.append_row(header)
    return ws


def get_or_create_worksheet(name, header):
    return get_worksheet(name, tuple(header))


@st.cache_data(ttl=CACHE_TTL)
def read_df(name, header):
    ws = get_or_create_worksheet(name, header)
    data = ws.get_all_records()
    return pd.DataFrame(data) if data else pd.DataFrame(columns=header)


def invalidate_cache():
    read_df.clear()


def append_row(name, header, row):
    ws = get_or_create_worksheet(name, header)
    ws.append_row(row)
    invalidate_cache()


def overwrite_sheet(name, header, df):
    ws = get_or_create_worksheet(name, header)
    ws.clear()
    ws.append_row(header)
    if not df.empty:
        ws.append_rows(df.astype(str).values.tolist())
    invalidate_cache()


def delete_rows_by_index(name, header, df_indices):
    """Cancella dal foglio le righe corrispondenti agli indici del DataFrame.
    df_indices: lista di indici (0-based) relativi al DataFrame letto da read_df.
    """
    ws = get_or_create_worksheet(name, header)
    # +2: +1 per l'header, +1 perché gspread usa righe 1-based
    sheet_rows = sorted([i + 2 for i in df_indices], reverse=True)
    for row_num in sheet_rows:
        ws.delete_rows(row_num)
    invalidate_cache()


def now_italy():
    """Orario server (UTC su Streamlit Cloud) + 2 ore fisse per l'Italia (CEST)."""
    return datetime.now() + timedelta(hours=2)


# ---------- INTERFACCIA ----------
st.title("🏁 Gara a Tappe — Gestione Live")

tab_setup, tab_input, tab_chart, tab_data = st.tabs(
    ["⚙️ Impostazioni", "➡️ Registra arrivo", "📈 Grafico live", "📋 Dati"]
)

# ---------- IMPOSTAZIONI ----------
with tab_setup:
    st.subheader("Partecipanti")
    part_df = read_df("Partecipanti", PART_HEADER)
    st.dataframe(part_df, width="stretch")

    uploaded = st.file_uploader(
        "Carica lista partecipanti (CSV con colonne 'pettorale' e 'nome')", type="csv"
    )
    if uploaded is not None:
        new_df = pd.read_csv(uploaded)
        new_df.columns = [c.strip().lower() for c in new_df.columns]
        if "pettorale" not in new_df.columns or "nome" not in new_df.columns:
            st.error("Il CSV deve avere le colonne 'pettorale' e 'nome'.")
        else:
            if st.button("Importa (sovrascrive lista attuale)"):
                overwrite_sheet("Partecipanti", PART_HEADER, new_df[["pettorale", "nome"]])
                st.success("Lista importata!")
                st.rerun()

    with st.expander("Aggiungi partecipante singolo"):
        c1, c2 = st.columns(2)
        pett = c1.text_input("Pettorale", key="new_pett")
        nome = c2.text_input("Nome", key="new_nome")
        if st.button("Aggiungi partecipante"):
            if pett and nome:
                append_row("Partecipanti", PART_HEADER, [pett, nome])
                st.success("Aggiunto!")
                st.rerun()

    st.divider()
    st.subheader("Tappe (in ordine)")
    tappe_df = read_df("Tappe", TAPPE_HEADER)
    st.dataframe(tappe_df, width="stretch")

    tappe_input = st.text_area(
        "Elenco tappe in ordine, separate da virgola (es: Partenza, Tappa 1, Tappa 2, Arrivo)"
    )
    if st.button("Salva elenco tappe (sovrascrive)"):
        nomi = [t.strip() for t in tappe_input.split(",") if t.strip()]
        if nomi:
            df = pd.DataFrame({"ordine": range(1, len(nomi) + 1), "tappa": nomi})
            overwrite_sheet("Tappe", TAPPE_HEADER, df)
            st.success("Tappe salvate!")
            st.rerun()

# ---------- REGISTRA ARRIVO ----------
with tab_input:
    part_df = read_df("Partecipanti", PART_HEADER)
    tappe_df = read_df("Tappe", TAPPE_HEADER)

    if part_df.empty or tappe_df.empty:
        st.warning("Configura prima partecipanti e tappe nella scheda ⚙️ Impostazioni.")
    else:
        st.subheader("Registra passaggio")
        tappa_sel = st.selectbox(
            "Da quale tappa sta ripartendo il partecipante", tappe_df["tappa"].tolist()
        )
        opzioni = part_df.apply(lambda r: f"{r['pettorale']} - {r['nome']}", axis=1).tolist()
        part_sel = st.selectbox("Partecipante", opzioni)

        if "orario_default" not in st.session_state:
            st.session_state["orario_default"] = now_italy().time()
        if "orario_key_counter" not in st.session_state:
            st.session_state["orario_key_counter"] = 0

        col_a, col_b = st.columns([3, 1])
        with col_a:
            orario = st.time_input(
                "Orario",
                key=f"orario_input_{st.session_state['orario_key_counter']}",
                value=st.session_state["orario_default"],
            )
        with col_b:
            if st.button("🔄 Orario live"):
                st.session_state["orario_default"] = now_italy().time()
                st.session_state["orario_key_counter"] += 1
                st.rerun()

        if st.button("✅ Registra arrivo", type="primary", width="stretch"):
            pett, nome = part_sel.split(" - ", 1)
            ts = datetime.combine(now_italy().date(), orario)
            append_row(
                "Arrivi",
                ARRIVI_HEADER,
                [pett, nome, tappa_sel, ts.strftime("%Y-%m-%d %H:%M:%S")],
            )
            st.success(f"Registrato: {nome} è partito da {tappa_sel} alle {orario.strftime('%H:%M')}")

# ---------- GRAFICO LIVE ----------
with tab_chart:
    st_autorefresh(interval=60000, key="chart_refresh")

    tappe_df = read_df("Tappe", TAPPE_HEADER)
    arrivi_df = read_df("Arrivi", ARRIVI_HEADER)

    if tappe_df.empty:
        st.info("Nessuna tappa configurata ancora.")
    elif arrivi_df.empty:
        st.info("Nessun arrivo registrato ancora.")
    else:
        tappe_df["ordine"] = pd.to_numeric(tappe_df["ordine"])
        ordine_map = dict(zip(tappe_df["tappa"], tappe_df["ordine"]))
        arrivi_df["ordine"] = arrivi_df["tappa"].map(ordine_map)
        arrivi_df["timestamp"] = pd.to_datetime(arrivi_df["timestamp"])
        arrivi_df = arrivi_df.sort_values("timestamp")

        fig = go.Figure()
        for nome, g in arrivi_df.groupby("nome"):
            g = g.sort_values("timestamp")
            fig.add_trace(
                go.Scatter(
                    x=g["timestamp"],
                    y=g["ordine"],
                    mode="lines+markers+text",
                    name=nome,
                    text=g["tappa"],
                    textposition="top center",
                )
            )

        fig.update_yaxes(
            tickmode="array",
            tickvals=tappe_df["ordine"],
            ticktext=tappe_df["tappa"],
            title="Tappa raggiunta",
        )
        fig.update_xaxes(title="Orario")
        fig.update_layout(height=600, legend_title="Partecipante")
        st.plotly_chart(fig, width="stretch")

        st.caption(f"Il grafico si aggiorna automaticamente ogni 60 secondi (dati in cache per {CACHE_TTL}s).")

# ---------- DATI ----------
with tab_data:
    st.subheader("Tutti gli arrivi registrati")
    arrivi_df = read_df("Arrivi", ARRIVI_HEADER)

    if arrivi_df.empty:
        st.info("Nessun arrivo registrato ancora.")
    else:
        arrivi_df_sorted = arrivi_df.sort_values("timestamp", ascending=False)
        st.dataframe(arrivi_df_sorted, width="stretch")

        st.divider()
        st.subheader("🗑️ Cancella registrazioni")

        arrivi_df_reset = arrivi_df.reset_index()
        arrivi_df_reset["label"] = arrivi_df_reset.apply(
            lambda r: f"[{r['index']}] {r['pettorale']} - {r['nome']} | {r['tappa']} | {r['timestamp']}",
            axis=1,
        )

        selezionati = st.multiselect(
            "Seleziona una o più registrazioni da cancellare",
            options=arrivi_df_reset["label"].tolist(),
        )

        if selezionati:
            st.warning(f"Stai per cancellare {len(selezionati)} registrazione/i. L'operazione è irreversibile.")
            conferma = st.checkbox("Confermo di voler cancellare le registrazioni selezionate")

            if st.button("❌ Cancella selezionati", type="primary", disabled=not conferma):
                indici_da_cancellare = arrivi_df_reset[
                    arrivi_df_reset["label"].isin(selezionati)
                ]["index"].tolist()

                delete_rows_by_index("Arrivi", ARRIVI_HEADER, indici_da_cancellare)
                st.success(f"Cancellate {len(indici_da_cancellare)} registrazione/i!")
                st.rerun()
