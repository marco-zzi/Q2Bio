
import io
import math
import re
import threading
import time
from dataclasses import dataclass, field

import qrcode
import streamlit as st
from phe import paillier
from phe.paillier import EncryptedNumber
from streamlit_autorefresh import st_autorefresh


# ============================================================
# ZEBRA — QR PRIVATE CLINICAL SIMILARITY DEMO
# ============================================================
# Hackathon proof-of-concept only.
# Use SYNTHETIC patient descriptions.
#
# Streamlit receives the form values in this simplified demo.
# The app then:
#   1) deliberately excludes name + city from the vector,
#   2) encodes age, gender, structured features + symptom text,
#   3) encrypts the encoded representation,
#   4) stores only encrypted representations in shared state,
#   5) releases only the final similarity score on the screen.
#
# The homomorphic demonstration uses Paillier:
#   - A is stored encrypted.
#   - B is encoded and encrypted for storage.
#   - B's quantized vector is used transiently to evaluate an
#     encrypted dot product against A's ciphertext.
#   - only the resulting scalar is decrypted.
#
# This is NOT a production healthcare privacy implementation.
# ============================================================

st.set_page_config(
    page_title="Zebra Demo",
    page_icon="🦓",
    layout="centered",
)

st.markdown(
    """
    <style>
    .block-container {max-width: 900px; padding-top: 2rem;}
    .zebra-title {font-size: 2.1rem; font-weight: 780; margin-bottom: .15rem;}
    .zebra-sub {color: #68737d; margin-bottom: 1.2rem;}
    .privacy-box {
        border: 1px solid #d9e0e5; border-radius: 12px;
        padding: .8rem 1rem; background: #f7f9fa; margin: .8rem 0;
    }
    .score {
        font-size: 5.2rem; font-weight: 850; text-align: center;
        line-height: 1.05; margin-top: 2rem;
    }
    .score-label {text-align:center; font-size:1.25rem; font-weight:650;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------
# Feature extraction
# ---------------------------

SYMPTOM_MAP = [
    ("bloating", ["bloating", "abdominal distension", "distended abdomen", "swollen abdomen"]),
    ("early_satiety", ["early satiety", "full quickly", "feeling full quickly", "poor appetite", "reduced appetite"]),
    ("pelvic_pain_pressure", ["pelvic pain", "pelvic pressure", "pelvic discomfort"]),
    ("urinary_frequency_urgency", ["urinary frequency", "urinary urgency", "frequent urination", "urinating often"]),
    ("fatigue", ["fatigue", "tiredness", "tired", "low energy"]),
    ("abdominal_pain_discomfort", ["abdominal pain", "abdominal discomfort", "stomach pain"]),
    ("bowel_change", ["constipation", "diarrhea", "diarrhoea", "bowel changes"]),
    ("nausea_vomiting", ["nausea", "vomiting", "vomited", "feeling sick"]),
    ("weight_loss_text", ["weight loss", "lost weight", "unintentional weight"]),
    ("abnormal_bleeding", ["abnormal bleeding", "postmenopausal bleeding", "vaginal bleeding"]),
    ("vertigo_dizziness", ["vertigo", "dizziness", "dizzy", "room spinning"]),
    ("chest_discomfort", ["chest pain", "chest discomfort", "chest pressure", "chest tightness"]),
    ("shortness_of_breath", ["shortness of breath", "breathless", "dyspnea", "dyspnoea"]),
    ("back_jaw_arm_discomfort", ["back pain", "jaw pain", "arm pain", "shoulder pain"]),
    ("neurological_signs", ["weakness", "numbness", "slurred speech", "double vision", "ataxia"]),
    ("headache", ["headache", "severe headache"]),
    ("loss_of_balance", ["loss of balance", "unsteady", "gait instability", "difficulty walking"]),
    ("palpitations", ["palpitations", "heart racing", "rapid heartbeat"]),
]

VECTOR_SCALE = 10_000


def persistence_score(text: str) -> float:
    low = text.lower()
    months = []
    for m in re.finditer(r"(\d{1,2})\s*(?:month|months|mo\b)", low):
        months.append(float(m.group(1)))
    for m in re.finditer(r"(\d{1,2})\s*(?:week|weeks|wk\b)", low):
        months.append(float(m.group(1)) / 4.345)

    if months:
        return max(0.0, min(1.0, max(months) / 12.0))

    if any(k in low for k in ("persistent", "progressive", "recurrent", "longstanding", "long-standing")):
        return 0.6

    return 0.0


def encode_patient(
    age: int,
    gender: str,
    pain: int,
    fever: bool,
    weight_loss: bool,
    worsening: bool,
    night_symptoms: bool,
    symptom_text: str,
):
    """
    Name and city are intentionally NOT accepted as arguments.
    Therefore they cannot enter the feature vector.
    """

    age_v = max(0.0, min(float(age), 120.0)) / 100.0

    # First vector entries:
    # [age, gender_female, gender_male, gender_other/unknown]
    g_f = 1.0 if gender == "Female" else 0.0
    g_m = 1.0 if gender == "Male" else 0.0
    g_o = 1.0 if gender in ("Other", "Unknown / not entered") else 0.0

    structured = [
        age_v,
        g_f,
        g_m,
        g_o,
        max(0.0, min(float(pain), 10.0)) / 10.0,
        1.0 if fever else 0.0,
        1.0 if weight_loss else 0.0,
        1.0 if worsening else 0.0,
        1.0 if night_symptoms else 0.0,
    ]

    low = symptom_text.lower()
    detected = []
    symptom_values = []

    for label, phrases in SYMPTOM_MAP:
        hit = any(p in low for p in phrases)
        symptom_values.append(1.0 if hit else 0.0)
        if hit:
            detected.append(label)

    persist = persistence_score(symptom_text)
    if persist > 0:
        detected.append("persistence")

    raw = structured + symptom_values + [persist]

    # Unit-normalize so dot product approximates cosine similarity.
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    normalized = [v / norm for v in raw]
    quantized = [max(0, int(round(v * VECTOR_SCALE))) for v in normalized]

    labels = [
        "age",
        "gender_female",
        "gender_male",
        "gender_other_or_unknown",
        "pain_level",
        "fever",
        "weight_loss",
        "worsening",
        "night_symptoms",
    ] + [name for name, _ in SYMPTOM_MAP] + ["persistence"]

    return labels, normalized, quantized, detected


# ---------------------------
# Shared in-memory demo state
# ---------------------------

@dataclass
class DemoSession:
    public_key: object
    private_key: object

    encrypted_a: list = field(default_factory=list)
    encrypted_b: list = field(default_factory=list)

    a_ready: bool = False
    b_ready: bool = False

    a_cipher_preview: str | None = None
    b_cipher_preview: str | None = None

    score: float | None = None
    updated: float = field(default_factory=time.time)


class DemoStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}

    def get(self, session_id: str) -> DemoSession:
        with self.lock:
            if session_id not in self.sessions:
                pub, priv = paillier.generate_paillier_keypair(n_length=1024)
                self.sessions[session_id] = DemoSession(pub, priv)
            return self.sessions[session_id]

    def reset(self, session_id: str):
        with self.lock:
            pub, priv = paillier.generate_paillier_keypair(n_length=1024)
            self.sessions[session_id] = DemoSession(pub, priv)


@st.cache_resource
def get_store():
    return DemoStore()


STORE = get_store()


# ---------------------------
# Helpers
# ---------------------------

def qp(name: str, default: str) -> str:
    value = st.query_params.get(name, default)
    if isinstance(value, list):
        return value[0] if value else default
    return str(value)


ROLE = qp("role", "admin").strip().upper()
SESSION_ID = qp("session", "demo").strip() or "demo"


def make_url(base_url: str, role: str, session_id: str) -> str:
    base = base_url.strip().rstrip("/")
    return f"{base}/?role={role}&session={session_id}"


def qr_png(url: str) -> bytes:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def ciphertext_preview(enc: EncryptedNumber) -> str:
    txt = str(enc.ciphertext())
    return txt[:48] + "…" if len(txt) > 48 else txt


def reconstruct_encrypted(pk, stored):
    return [EncryptedNumber(pk, c, exp) for c, exp in stored]


def store_encrypted_vector(vec_int, session: DemoSession, which: str):
    encrypted = [session.public_key.encrypt(v) for v in vec_int]
    serial = [(x.ciphertext(), x.exponent) for x in encrypted]

    if which == "A":
        session.encrypted_a = serial
        session.a_ready = True
        session.a_cipher_preview = ciphertext_preview(encrypted[0])
    else:
        session.encrypted_b = serial
        session.b_ready = True
        session.b_cipher_preview = ciphertext_preview(encrypted[0])

    session.updated = time.time()
    return encrypted


def protected_similarity_with_a_encrypted(
    a_encrypted,
    b_quantized,
    session: DemoSession,
):
    """
    Paillier supports Enc(a) * plaintext_scalar.
    Therefore we evaluate:
        Enc(sum_i a_i * b_i)
    while B's quantized vector is used transiently.
    Only the final scalar is decrypted.
    """
    terms = [a_encrypted[i] * int(b_quantized[i]) for i in range(len(b_quantized))]
    enc_dot = terms[0]
    for term in terms[1:]:
        enc_dot = enc_dot + term

    dot_int = session.private_key.decrypt(enc_dot)
    score = dot_int / float(VECTOR_SCALE * VECTOR_SCALE)
    return max(0.0, min(1.0, score))


def render_header(subtitle: str):
    st.markdown('<div class="zebra-title">🦓 Zebra</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="zebra-sub">{subtitle}</div>', unsafe_allow_html=True)


# ---------------------------
# Juror form
# ---------------------------

def juror_page(role: str):
    render_header(f"Private clinical similarity demo — Juror {role}")
    st.caption(f"Demo session: `{SESSION_ID}`")

    st.info(
        "Use a **synthetic patient**. This simplified Streamlit demo receives the form "
        "on the app server; name and city are then deliberately excluded from the vector. "
        "The production concept would perform this step client-side."
    )

    with st.form(f"juror_form_{role}"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Name", placeholder="e.g. Anna Example")
            age = st.number_input("Age", min_value=0, max_value=120, value=58, step=1)
        with c2:
            city = st.text_input("City", placeholder="e.g. Kaunas")
            gender = st.selectbox(
                "Gender / sex (demo field)",
                ["Female", "Male", "Other", "Unknown / not entered"],
            )

        pain = st.slider("Pain level", 0, 10, 3)

        c3, c4 = st.columns(2)
        with c3:
            fever = st.checkbox("Fever")
            weight_loss = st.checkbox("Unintentional weight loss")
        with c4:
            worsening = st.checkbox("Symptoms worsening")
            night_symptoms = st.checkbox("Symptoms wake patient at night")

        symptoms = st.text_area(
            "Symptoms / short clinical description",
            height=160,
            placeholder=(
                "Example: 8 months of progressive bloating, early satiety, "
                "pelvic pressure and urinary frequency with fatigue."
            ),
        )

        submitted = st.form_submit_button(
            "Encode → encrypt → submit",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        labels, normalized, quantized, detected = encode_patient(
            age=age,
            gender=gender,
            pain=pain,
            fever=fever,
            weight_loss=weight_loss,
            worsening=worsening,
            night_symptoms=night_symptoms,
            symptom_text=symptoms,
        )

        # Name + city are never passed to the encoding function.
        st.markdown(
            """
            <div class="privacy-box">
            <b>De-identification step</b><br>
            Deleted from model input: <b>name, city</b><br>
            Retained: age, gender block, structured clinical fields, recognized symptoms.
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.write("**Recognized clinical concepts:**")
        if detected:
            st.write(", ".join(x.replace("_", " ") for x in detected))
        else:
            st.write("_No demo symptom concepts recognized._")

        with st.expander("Show encoded vector for demo transparency"):
            st.code(
                "[" + ", ".join(f"{v:.3f}" for v in normalized) + "]",
                language=None,
            )
            st.caption(
                "First entries are age and gender block, followed by pain/clinical flags, "
                "text-derived symptoms, and persistence."
            )

        session = STORE.get(SESSION_ID)

        with STORE.lock:
            if role == "A":
                encrypted_a = store_encrypted_vector(quantized, session, "A")
                st.success("Juror A vector encoded and encrypted.")
                st.code(f"Ciphertext preview: {session.a_cipher_preview}", language=None)
                st.info("Juror B can now submit their synthetic patient.")
            else:
                # Encrypt B for retained shared state.
                store_encrypted_vector(quantized, session, "B")

                if not session.a_ready:
                    st.warning(
                        "Juror A has not submitted yet. Your encrypted B representation is stored, "
                        "but for this simple Paillier demo please submit again after A is ready."
                    )
                else:
                    a_encrypted = reconstruct_encrypted(
                        session.public_key,
                        session.encrypted_a,
                    )
                    score = protected_similarity_with_a_encrypted(
                        a_encrypted,
                        quantized,
                        session,
                    )
                    session.score = score
                    session.updated = time.time()

                    st.success("Protected comparison completed.")
                    st.code(f"Ciphertext preview: {session.b_cipher_preview}", language=None)
                    st.write(
                        "Only the final similarity score is released on the **presentation screen**."
                    )

    session = STORE.get(SESSION_ID)
    st.divider()
    st.caption(
        f"Network status — A: {'✓' if session.a_ready else 'waiting'} · "
        f"B: {'✓' if session.b_ready else 'waiting'} · "
        f"score: {'ready' if session.score is not None else 'waiting'}"
    )


# ---------------------------
# Presentation screen
# ---------------------------

def screen_page():
    st_autorefresh(interval=1000, limit=None, key=f"screen_refresh_{SESSION_ID}")

    session = STORE.get(SESSION_ID)

    st.markdown(
        '<div style="text-align:center; margin-top:4rem; color:#69757f; '
        'font-size:.85rem; letter-spacing:.11em; font-weight:750;">'
        'ZEBRA · PRIVATE CLINICAL SIMILARITY</div>',
        unsafe_allow_html=True,
    )

    if session.score is None:
        st.markdown(
            '<div style="text-align:center; font-size:2.2rem; font-weight:750; '
            'margin-top:3rem;">Waiting for two protected patients…</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="text-align:center; color:#75808a; margin-top:.8rem;">'
            'No patient form or feature vector is shown on this screen.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="score">{session.score * 100:.1f}%</div>'
            '<div class="score-label">clinical similarity index</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="text-align:center; color:#75808a; margin-top:1.2rem;">'
            'Released output: similarity only.</div>',
            unsafe_allow_html=True,
        )


# ---------------------------
# Admin / QR page
# ---------------------------

def admin_page():
    render_header("Hackathon control panel")
    st.caption(f"Current session: `{SESSION_ID}`")

    st.write(
        "Deploy this repository to Streamlit Community Cloud. Enter the final public app URL "
        "below once, then download/print the QR codes."
    )

    base_default = st.session_state.get(
        "base_url",
        "https://YOUR-APP-NAME.streamlit.app",
    )
    base_url = st.text_input("Public Streamlit app URL", value=base_default)
    st.session_state["base_url"] = base_url

    session_input = st.text_input("Demo session ID", value=SESSION_ID)
    if session_input != SESSION_ID:
        st.caption(
            f"To use session `{session_input}`, open the admin page with "
            f"`?role=admin&session={session_input}`."
        )

    a_url = make_url(base_url, "A", SESSION_ID)
    b_url = make_url(base_url, "B", SESSION_ID)
    s_url = make_url(base_url, "screen", SESSION_ID)

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Juror A")
        a_png = qr_png(a_url)
        st.image(a_png, width=220)
        st.code(a_url, language=None)
        st.download_button(
            "Download QR A",
            data=a_png,
            file_name=f"zebra_QR_A_{SESSION_ID}.png",
            mime="image/png",
            use_container_width=True,
        )

    with c2:
        st.subheader("Juror B")
        b_png = qr_png(b_url)
        st.image(b_png, width=220)
        st.code(b_url, language=None)
        st.download_button(
            "Download QR B",
            data=b_png,
            file_name=f"zebra_QR_B_{SESSION_ID}.png",
            mime="image/png",
            use_container_width=True,
        )

    st.subheader("Presentation screen")
    st.code(s_url, language=None)
    st.link_button("Open presentation screen", s_url, use_container_width=True)

    session = STORE.get(SESSION_ID)
    st.divider()
    c3, c4, c5 = st.columns(3)
    c3.metric("Juror A", "Ready" if session.a_ready else "Waiting")
    c4.metric("Juror B", "Ready" if session.b_ready else "Waiting")
    c5.metric(
        "Similarity",
        f"{session.score * 100:.1f}%" if session.score is not None else "—",
    )

    if st.button("Reset this demo session", use_container_width=True):
        STORE.reset(SESSION_ID)
        st.success("Session reset.")
        st.rerun()

    with st.expander("What this simplified demo does / does not prove"):
        st.write(
            """
            **It demonstrates**
            - QR → juror form → de-identification logic → vectorization → encryption → similarity.
            - Name and city are excluded from the feature vector.
            - Only encrypted representations are retained in shared demo state.
            - The presentation screen releases only the similarity score.
            - A real Paillier homomorphic operation is used for the dot-product demonstration.

            **It does not claim**
            - End-to-end patient privacy from Streamlit Cloud itself.
            - Production-grade cryptographic key management.
            - Production medical NLP or validated diagnosis.
            - Regulatory approval.

            For the hackathon, use synthetic cases only.
            """
        )


# ---------------------------
# Router
# ---------------------------

if ROLE == "A":
    juror_page("A")
elif ROLE == "B":
    juror_page("B")
elif ROLE == "SCREEN":
    screen_page()
else:
    admin_page()
