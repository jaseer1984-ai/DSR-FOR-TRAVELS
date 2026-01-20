import os
from datetime import date

import streamlit as st
import pandas as pd
import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor

APP_TITLE = "✈️ Travel Agency DSR (Sales • Refund • Receipt • ADM)"
st.set_page_config(page_title="Travel DSR", layout="wide")
st.title(APP_TITLE)

# =========================
# COLORFUL UI (CSS)
# =========================
st.markdown(
    """
<style>
/* Page background */
.stApp {
    background: linear-gradient(180deg, #f6f8ff 0%, #ffffff 60%);
}

/* Title */
h1 {
    color: #1f2a44 !important;
    font-weight: 900 !important;
}

/* Card container */
.dsr-card {
    background: #ffffff;
    border: 1px solid #e8ecff;
    border-radius: 18px;
    padding: 18px 18px 10px 18px;
    box-shadow: 0 6px 18px rgba(31, 42, 68, 0.08);
    margin-bottom: 14px;
}

/* Section header */
.dsr-header {
    background: linear-gradient(90deg, #3b82f6 0%, #8b5cf6 50%, #ec4899 100%);
    color: white;
    border-radius: 14px;
    padding: 10px 14px;
    font-weight: 800;
    letter-spacing: 0.2px;
    margin-bottom: 12px;
}

/* Inputs */
div[data-baseweb="input"] > div,
div[data-baseweb="textarea"] > div,
div[data-baseweb="select"] > div {
    border-radius: 14px !important;
    border: 1px solid #dbe3ff !important;
}

/* Focus glow */
div[data-baseweb="input"] > div:focus-within,
div[data-baseweb="textarea"] > div:focus-within,
div[data-baseweb="select"] > div:focus-within {
    border: 1px solid #3b82f6 !important;
    box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.15) !important;
}

/* Buttons */
.stButton > button, .stDownloadButton > button {
    background: linear-gradient(90deg, #3b82f6 0%, #8b5cf6 60%, #ec4899 100%) !important;
    color: white !important;
    border: 0 !important;
    border-radius: 14px !important;
    padding: 10px 14px !important;
    font-weight: 800 !important;
    box-shadow: 0 8px 16px rgba(59, 130, 246, 0.18);
}
.stButton > button:hover, .stDownloadButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 10px 18px rgba(59, 130, 246, 0.22);
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #111827 0%, #1f2a44 100%);
}
section[data-testid="stSidebar"] * {
    color: #e5e7eb !important;
}
</style>
""",
    unsafe_allow_html=True,
)


# =========================
# SECRETS / DATABASE URL
# =========================
def read_database_url() -> str | None:
    if hasattr(st, "secrets") and "DATABASE_URL" in st.secrets:
        return str(st.secrets["DATABASE_URL"]).strip()
    return os.getenv("DATABASE_URL")


DATABASE_URL = read_database_url()
if not DATABASE_URL:
    st.error("DATABASE_URL is missing. Add it in Streamlit Cloud → Settings → Secrets.")
    st.stop()


def _normalize_db_url(url: str) -> str:
    url = url.strip().strip('"').strip("'")
    url = url.replace("&channel_binding=require", "")
    url = url.replace("?channel_binding=require", "?")
    url = url.replace("?&", "?").replace("??", "?")
    if url.endswith("?") or url.endswith("&"):
        url = url[:-1]
    if "sslmode=" not in url:
        url = url + ("&sslmode=require" if "?" in url else "?sslmode=require")
    url = url.replace("?&", "?")
    if url.endswith("?") or url.endswith("&"):
        url = url[:-1]
    return url


DATABASE_URL = _normalize_db_url(DATABASE_URL)


def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# =========================
# DB INIT
# =========================
def init_db():
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash BYTEA NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','staff')),
            staff_name TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS opening_outstanding (
            staff_user_id INTEGER PRIMARY KEY REFERENCES users(id),
            opening_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            staff_user_id INTEGER NOT NULL REFERENCES users(id),

            txn_date DATE NOT NULL,
            entry_type TEXT NOT NULL CHECK(entry_type IN ('SALE','REFUND','RECEIPT','ADM')),

            ai_code TEXT,
            ticket_number TEXT,
            passenger_name TEXT,
            route TEXT,
            supplier TEXT,

            reference_no TEXT,
            notes TEXT,

            basic_fare NUMERIC(14,2) NOT NULL DEFAULT 0,
            comm NUMERIC(14,2) NOT NULL DEFAULT 0,
            net_to_supp NUMERIC(14,2) NOT NULL DEFAULT 0,
            bill_to_customer NUMERIC(14,2) NOT NULL DEFAULT 0,

            receipt_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
            adm_amount NUMERIC(14,2) NOT NULL DEFAULT 0,

            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)

        conn.commit()
    finally:
        conn.close()


def users_exist() -> bool:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users;")
        return cur.fetchone()[0] > 0
    finally:
        conn.close()


# =========================
# AUTH
# =========================
def create_user(username: str, password: str, role: str, staff_name: str):
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users(username, password_hash, role, staff_name) VALUES (%s,%s,%s,%s)",
            (username.strip(), psycopg2.Binary(pw_hash), role, staff_name.strip())
        )
        conn.commit()
    finally:
        conn.close()


def verify_login(username: str, password: str):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, username, password_hash, role, staff_name, active
            FROM users
            WHERE username=%s
        """, (username.strip(),))
        row = cur.fetchone()
    finally:
        conn.close()

    if not row or not row["active"]:
        return None

    pw_hash = bytes(row["password_hash"])
    if bcrypt.checkpw(password.encode("utf-8"), pw_hash):
        return {"id": row["id"], "username": row["username"], "role": row["role"], "staff_name": row["staff_name"]}
    return None


def list_users():
    conn = get_conn()
    try:
        return pd.read_sql("""
            SELECT id, username, staff_name, role, active, created_at
            FROM users
            ORDER BY role, staff_name
        """, conn)
    finally:
        conn.close()


def set_user_active(user_id: int, active: bool):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET active=%s WHERE id=%s", (active, user_id))
        conn.commit()
    finally:
        conn.close()


# =========================
# OUTSTANDING
# =========================
def set_opening_outstanding(staff_id: int, amount: float):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO opening_outstanding(staff_user_id, opening_amount)
            VALUES (%s, %s)
            ON CONFLICT (staff_user_id)
            DO UPDATE SET opening_amount = EXCLUDED.opening_amount, updated_at = NOW();
        """, (staff_id, float(amount)))
        conn.commit()
    finally:
        conn.close()


def get_opening_outstanding(staff_id: int) -> float:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT opening_amount FROM opening_outstanding WHERE staff_user_id=%s", (staff_id,))
        r = cur.fetchone()
        return float(r[0]) if r else 0.0
    finally:
        conn.close()


def compute_outstanding(staff_id: int) -> float:
    opening = get_opening_outstanding(staff_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(
                CASE
                    WHEN entry_type='SALE' THEN bill_to_customer
                    WHEN entry_type='REFUND' THEN -bill_to_customer
                    WHEN entry_type='RECEIPT' THEN -receipt_amount
                    WHEN entry_type='ADM' THEN adm_amount
                    ELSE 0
                END
            ), 0)
            FROM transactions
            WHERE staff_user_id=%s
        """, (staff_id,))
        movement = float(cur.fetchone()[0] or 0)
        return opening + movement
    finally:
        conn.close()


# =========================
# TRANSACTIONS
# =========================
def add_transaction(
    staff_id: int,
    txn_date,
    entry_type: str,
    ai_code: str = "",
    ticket_number: str = "",
    passenger_name: str = "",
    route: str = "",
    supplier: str = "",
    reference_no: str = "",
    notes: str = "",
    basic_fare: float = 0,
    comm: float = 0,
    net_to_supp: float = 0,
    bill_to_customer: float = 0,
    receipt_amount: float = 0,
    adm_amount: float = 0,
):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO transactions(
                staff_user_id, txn_date, entry_type,
                ai_code, ticket_number, passenger_name, route, supplier,
                reference_no, notes,
                basic_fare, comm, net_to_supp, bill_to_customer,
                receipt_amount, adm_amount
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            staff_id, txn_date, entry_type,
            ai_code.strip() or None,
            ticket_number.strip() or None,
            passenger_name.strip() or None,
            route.strip() or None,
            supplier.strip() or None,
            reference_no.strip() or None,
            notes.strip() or None,
            float(basic_fare), float(comm), float(net_to_supp), float(bill_to_customer),
            float(receipt_amount), float(adm_amount)
        ))
        conn.commit()
    finally:
        conn.close()


def my_transactions_df(staff_id: int, start_d, end_d):
    conn = get_conn()
    try:
        return pd.read_sql("""
            SELECT txn_date AS "Date",
                   entry_type AS "Entry Type",
                   ai_code AS "AI Code",
                   ticket_number AS "Ticket Number",
                   passenger_name AS "Passenger Name",
                   route AS "Route",
                   supplier AS "Supplier",
                   reference_no AS "Ref No",
                   bill_to_customer AS "Bill to Customer",
                   receipt_amount AS "Receipt",
                   adm_amount AS "ADM",
                   notes AS "Notes",
                   created_at AS "Created At"
            FROM transactions
            WHERE staff_user_id=%s
              AND txn_date BETWEEN %s AND %s
            ORDER BY txn_date DESC, id DESC
        """, conn, params=(staff_id, start_d, end_d))
    finally:
        conn.close()


def all_transactions_df(start_d, end_d, text_filter: str):
    conn = get_conn()
    try:
        if text_filter and text_filter.strip():
            tf = f"%{text_filter.strip()}%"
            return pd.read_sql("""
                SELECT t.txn_date AS "Date",
                       u.staff_name AS "Staff Name",
                       u.username AS "Entered By",
                       t.entry_type AS "Entry Type",
                       t.ai_code AS "AI Code",
                       t.ticket_number AS "Ticket Number",
                       t.passenger_name AS "Passenger Name",
                       t.route AS "Route",
                       t.supplier AS "Supplier",
                       t.reference_no AS "Ref No",
                       t.bill_to_customer AS "Bill to Customer",
                       t.receipt_amount AS "Receipt",
                       t.adm_amount AS "ADM",
                       t.notes AS "Notes",
                       t.created_at AS "Created At"
                FROM transactions t
                JOIN users u ON u.id = t.staff_user_id
                WHERE t.txn_date BETWEEN %s AND %s
                  AND (
                      LOWER(u.staff_name) LIKE LOWER(%s)
                      OR LOWER(u.username) LIKE LOWER(%s)
                      OR LOWER(COALESCE(t.ticket_number,'')) LIKE LOWER(%s)
                      OR LOWER(COALESCE(t.reference_no,'')) LIKE LOWER(%s)
                  )
                ORDER BY t.txn_date DESC, t.id DESC
            """, conn, params=(start_d, end_d, tf, tf, tf, tf))
        else:
            return pd.read_sql("""
                SELECT t.txn_date AS "Date",
                       u.staff_name AS "Staff Name",
                       u.username AS "Entered By",
                       t.entry_type AS "Entry Type",
                       t.ai_code AS "AI Code",
                       t.ticket_number AS "Ticket Number",
                       t.passenger_name AS "Passenger Name",
                       t.route AS "Route",
                       t.supplier AS "Supplier",
                       t.reference_no AS "Ref No",
                       t.bill_to_customer AS "Bill to Customer",
                       t.receipt_amount AS "Receipt",
                       t.adm_amount AS "ADM",
                       t.notes AS "Notes",
                       t.created_at AS "Created At"
                FROM transactions t
                JOIN users u ON u.id = t.staff_user_id
                WHERE t.txn_date BETWEEN %s AND %s
                ORDER BY t.txn_date DESC, t.id DESC
            """, conn, params=(start_d, end_d))
    finally:
        conn.close()


def outstanding_summary_df():
    conn = get_conn()
    try:
        return pd.read_sql("""
            SELECT u.id,
                   u.staff_name AS "Staff Name",
                   u.username AS "Username",
                   COALESCE(o.opening_amount, 0) AS "Opening",
                   COALESCE(SUM(
                      CASE
                        WHEN t.entry_type='SALE' THEN t.bill_to_customer
                        WHEN t.entry_type='REFUND' THEN -t.bill_to_customer
                        WHEN t.entry_type='RECEIPT' THEN -t.receipt_amount
                        WHEN t.entry_type='ADM' THEN t.adm_amount
                        ELSE 0
                      END
                   ),0) AS "Movement",
                   COALESCE(o.opening_amount, 0) + COALESCE(SUM(
                      CASE
                        WHEN t.entry_type='SALE' THEN t.bill_to_customer
                        WHEN t.entry_type='REFUND' THEN -t.bill_to_customer
                        WHEN t.entry_type='RECEIPT' THEN -t.receipt_amount
                        WHEN t.entry_type='ADM' THEN t.adm_amount
                        ELSE 0
                      END
                   ),0) AS "Outstanding"
            FROM users u
            LEFT JOIN opening_outstanding o ON o.staff_user_id = u.id
            LEFT JOIN transactions t ON t.staff_user_id = u.id
            WHERE u.role='staff'
            GROUP BY u.id, u.staff_name, u.username, o.opening_amount
            ORDER BY u.staff_name
        """, conn)
    finally:
        conn.close()


# =========================
# STARTUP
# =========================
try:
    init_db()
except Exception:
    st.error("Database connection failed. Check DATABASE_URL in Streamlit Secrets (use Neon direct URL).")
    st.stop()


# =========================
# SESSION
# =========================
if "user" not in st.session_state:
    st.session_state.user = None


# =========================
# FIRST ADMIN SETUP
# =========================
if not users_exist():
    st.warning("First time setup: Create ADMIN account")
    st.markdown('<div class="dsr-card">', unsafe_allow_html=True)
    st.markdown('<div class="dsr-header">👑 Create Admin</div>', unsafe_allow_html=True)

    with st.form("create_admin"):
        a_user = st.text_input("Admin Username")
        a_name = st.text_input("Admin Name")
        a_pass = st.text_input("Admin Password", type="password")
        a_pass2 = st.text_input("Confirm Password", type="password")
        ok = st.form_submit_button("Create Admin")

    st.markdown("</div>", unsafe_allow_html=True)

    if ok:
        if not a_user or not a_name or not a_pass:
            st.error("Fill all fields.")
        elif a_pass != a_pass2:
            st.error("Passwords do not match.")
        else:
            try:
                create_user(a_user, a_pass, "admin", a_name)
                st.success("Admin created ✅ Refresh and login.")
            except Exception as e:
                st.error(f"Could not create admin: {e}")
    st.stop()


# =========================
# LOGIN
# =========================
if st.session_state.user is None:
    st.subheader("Login")

    st.markdown('<div class="dsr-card">', unsafe_allow_html=True)
    st.markdown('<div class="dsr-header">🔐 Login</div>', unsafe_allow_html=True)

    u = st.text_input("Username")
    p = st.text_input("Password", type="password")

    if st.button("Login"):
        user = verify_login(u, p)
        if user:
            st.session_state.user = user
            st.rerun()
        else:
            st.error("Invalid login or user inactive.")

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()


user = st.session_state.user


# =========================
# SIDEBAR NAV
# =========================
with st.sidebar:
    st.markdown("### ✅ Session")
    st.markdown(f"**User:** {user['staff_name']}")
    st.markdown(f"**Role:** {user['role']}")
    if st.button("Logout"):
        st.session_state.user = None
        st.rerun()

    if user["role"] == "staff":
        menu = st.radio("Menu", ["New Entry", "My Report"])
    else:
        menu = st.radio("Menu", ["All Transactions", "Users", "Outstanding Summary"])


# =========================
# STAFF UI
# =========================
if user["role"] == "staff":
    st.markdown("### Staff Dashboard")
    out = compute_outstanding(user["id"])
    st.success(f"💼 **Your Outstanding: {out:,.2f}**")

    if menu == "New Entry":
        st.markdown('<div class="dsr-card">', unsafe_allow_html=True)
        st.markdown('<div class="dsr-header">🧾 New Entry</div>', unsafe_allow_html=True)

        entry_type = st.selectbox("Entry Type", ["SALE", "REFUND", "RECEIPT", "ADM"])

        with st.form("entry_form"):
            txn_date = st.date_input("Date", value=date.today())
            notes = st.text_input("Notes (optional)")

            if entry_type in ["SALE", "REFUND"]:
                c1, c2 = st.columns(2)
                with c1:
                    ai_code = st.text_input("AI Code *")
                    ticket_number = st.text_input("Ticket Number *")
                    passenger_name = st.text_input("Passenger Name *")
                    route = st.text_input("Route")
                with c2:
                    supplier = st.text_input("Supplier")
                    basic_fare = st.number_input("Basic Fare", min_value=0.0, step=10.0)
                    comm = st.number_input("Comm", min_value=0.0, step=1.0)
                    net_to_supp = st.number_input("Net to supp", min_value=0.0, step=10.0)
                    bill_to_customer = st.number_input("Bill to Customer", min_value=0.0, step=10.0)

                reference_no = ""
                receipt_amount = 0.0
                adm_amount = 0.0

            elif entry_type == "RECEIPT":
                st.caption("Receipt will **reduce** outstanding.")
                reference_no = st.text_input("Receipt No / Ref No *")
                receipt_amount = st.number_input("Receipt Amount *", min_value=0.0, step=10.0)

                ai_code = ticket_number = passenger_name = route = supplier = ""
                basic_fare = comm = net_to_supp = bill_to_customer = 0.0
                adm_amount = 0.0

            else:  # ADM
                st.caption("ADM will **increase** outstanding.")
                reference_no = st.text_input("ADM No / Ref No *")
                adm_amount = st.number_input("ADM Amount *", min_value=0.0, step=10.0)

                ai_code = ticket_number = passenger_name = route = supplier = ""
                basic_fare = comm = net_to_supp = bill_to_customer = 0.0
                receipt_amount = 0.0

            save = st.form_submit_button("Save Entry")

        st.markdown("</div>", unsafe_allow_html=True)

        if save:
            if entry_type in ["SALE", "REFUND"]:
                if not ai_code.strip() or not ticket_number.strip() or not passenger_name.strip():
                    st.error("AI Code, Ticket Number, Passenger Name are required.")
                else:
                    add_transaction(
                        staff_id=user["id"],
                        txn_date=txn_date,
                        entry_type=entry_type,
                        ai_code=ai_code,
                        ticket_number=ticket_number,
                        passenger_name=passenger_name,
                        route=route,
                        supplier=supplier,
                        reference_no=reference_no,
                        notes=notes,
                        basic_fare=basic_fare,
                        comm=comm,
                        net_to_supp=net_to_supp,
                        bill_to_customer=bill_to_customer,
                        receipt_amount=receipt_amount,
                        adm_amount=adm_amount,
                    )
                    st.success("Saved ✅")
                    st.rerun()

            elif entry_type == "RECEIPT":
                if not reference_no.strip() or receipt_amount <= 0:
                    st.error("Receipt Ref No and Receipt Amount are required.")
                else:
                    add_transaction(
                        staff_id=user["id"],
                        txn_date=txn_date,
                        entry_type=entry_type,
                        reference_no=reference_no,
                        notes=notes,
                        receipt_amount=receipt_amount,
                    )
                    st.success("Receipt saved ✅ Outstanding updated.")
                    st.rerun()

            else:  # ADM
                if not reference_no.strip() or adm_amount <= 0:
                    st.error("ADM Ref No and ADM Amount are required.")
                else:
                    add_transaction(
                        staff_id=user["id"],
                        txn_date=txn_date,
                        entry_type=entry_type,
                        reference_no=reference_no,
                        notes=notes,
                        adm_amount=adm_amount,
                    )
                    st.success("ADM saved ✅ Outstanding updated.")
                    st.rerun()

    else:  # My Report
        st.markdown('<div class="dsr-card">', unsafe_allow_html=True)
        st.markdown('<div class="dsr-header">📊 My Report</div>', unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        with c1:
            start = st.date_input("From", value=date.today().replace(day=1), key="my_from")
        with c2:
            end = st.date_input("To", value=date.today(), key="my_to")

        df = my_transactions_df(user["id"], start, end)
        st.dataframe(df, use_container_width=True)

        st.download_button(
            "Download My CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="my_report.csv",
            mime="text/csv",
        )

        st.markdown("</div>", unsafe_allow_html=True)


# =========================
# ADMIN UI
# =========================
else:
    st.markdown("## Admin Dashboard")

    if menu == "All Transactions":
        st.markdown('<div class="dsr-card">', unsafe_allow_html=True)
        st.markdown('<div class="dsr-header">🧾 All Transactions</div>', unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3)
        with c1:
            text_filter = st.text_input("Filter (staff/user/ticket/ref)")
        with c2:
            start = st.date_input("From", value=date.today().replace(day=1), key="a_from")
        with c3:
            end = st.date_input("To", value=date.today(), key="a_to")

        df = all_transactions_df(start, end, text_filter)
        st.dataframe(df, use_container_width=True)

        st.download_button(
            "Download CSV (Filtered)",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="all_transactions.csv",
            mime="text/csv",
        )

        st.markdown("</div>", unsafe_allow_html=True)

    elif menu == "Users":
        st.markdown('<div class="dsr-card">', unsafe_allow_html=True)
        st.markdown('<div class="dsr-header">👥 Users</div>', unsafe_allow_html=True)

        st.markdown("#### Create Staff User")
        with st.form("create_staff"):
            nu = st.text_input("Username (login)")
            nn = st.text_input("Staff Name")
            np = st.text_input("Password", type="password")
            create = st.form_submit_button("Create Staff")

        if create:
            if not nu.strip() or not nn.strip() or not np:
                st.error("Fill all fields.")
            else:
                try:
                    create_user(nu, np, "staff", nn)
                    st.success("Staff created ✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        st.markdown("#### Users List")
        users_df = list_users()
        display_df = users_df.rename(columns={
            "id": "ID",
            "username": "Username",
            "staff_name": "Staff Name",
            "role": "Role",
            "active": "Status",
            "created_at": "Created At",
        })
        display_df["Status"] = display_df["Status"].apply(lambda x: "Active" if x else "Inactive")
        st.dataframe(display_df, use_container_width=True)

        st.markdown("#### Activate / Deactivate User")
        if not users_df.empty:
            user_map = {f"{r['staff_name']} ({r['username']})": int(r["id"]) for _, r in users_df.iterrows()}
            selected = st.selectbox("Select User", list(user_map.keys()))
            uid = user_map[selected]
            status = st.selectbox("Status", ["Active", "Inactive"])
            new_status = True if status == "Active" else False
            if st.button("Update Status"):
                set_user_active(uid, new_status)
                st.success("Updated ✅")
                st.rerun()

        st.markdown("#### Opening Outstanding (per staff)")
        staff_only = users_df[users_df["role"] == "staff"].copy()
        if not staff_only.empty:
            staff_map = {f"{r['staff_name']} ({r['username']})": int(r["id"]) for _, r in staff_only.iterrows()}
            ssel = st.selectbox("Select Staff", list(staff_map.keys()), key="open_staff")
            sid = staff_map[ssel]
            current_open = get_opening_outstanding(sid)
            amt = st.number_input("Opening Outstanding", value=float(current_open), step=100.0)
            if st.button("Save Opening Outstanding"):
                set_opening_outstanding(sid, amt)
                st.success("Saved ✅")
                st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    else:  # Outstanding Summary
        st.markdown('<div class="dsr-card">', unsafe_allow_html=True)
        st.markdown('<div class="dsr-header">💰 Outstanding Summary</div>', unsafe_allow_html=True)

        summ = outstanding_summary_df()
        st.dataframe(summ, use_container_width=True)

        st.download_button(
            "Download Outstanding Summary CSV",
            data=summ.to_csv(index=False).encode("utf-8"),
            file_name="outstanding_summary.csv",
            mime="text/csv",
        )

        st.markdown("</div>", unsafe_allow_html=True)
