import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime
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


def get_or_create_worksheet(name, header):
    ss = get_spreadsheet()
    try:
        ws = ss.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=1000, cols=max(10, len(header)))
        ws.append_row(header)
    return ws


def read_df(name, header):
    ws = get_or_create_worksheet(name, header)
    data = ws.get_all_records()
    return pd.DataFrame(data) if data else pd.DataFrame(columns=header)


def append_row(name, header, row):
    ws = get_or_create_worksheet(name, header)
    ws.append_row(row)


def overwrite_sheet(name, header, df):
    ws = get_or_create_worksheet(name, header)
    ws.clear()
    ws.append_row(header)
    if not df.empty:
        ws.append_rows(df.astype(str).values.tolist())


# ---------- INTERFACCIA ----------
st.title("🏁 Gara a Tappe — Gestione Live")

tab_setup, tab_input, tab_chart, tab_data = st.tabs(
    ["⚙️ Impostazioni", "➡️ Registra arrivo", "📈 Grafico live", "📋 Dati"]
)

# ---------- IMPOSTAZIONI ----------
with tab_setup:
    st.subheader("Partecipanti")
    part_df = read_df("Partecipanti", PART_HEADER)
    st.dataframe(part_df, use_container_width=True)

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
    st.dataframe(tappe_df, use_container_width=True)

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
        orario = st.time_input("Orario", value=datetime.now().time())

        if st.button("✅ Registra arrivo", type="primary", use_container_width=True):
            pett, nome = part_sel.split(" - ", 1)
            ts = datetime.combine(datetime.now().date(), orario)
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
        st.plotly_chart(fig, use_container_width=True)

        st.caption("Il grafico si aggiorna automaticamente ogni 8 secondi.")

# ---------- DATI ----------
with tab_data:
    st.subheader("Tutti gli arrivi registrati")
    arrivi_df = read_df("Arrivi", ARRIVI_HEADER)
    if not arrivi_df.empty:
        arrivi_df = arrivi_df.sort_values("timestamp", ascending=False)
    st.dataframe(arrivi_df, use_container_width=True)
