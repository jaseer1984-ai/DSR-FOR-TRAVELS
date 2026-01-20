import os
import streamlit as st
import pandas as pd
import bcrypt
from datetime import date
import psycopg2
from psycopg2.extras import RealDictCursor

# -------------------- CONFIG --------------------
APP_TITLE = "✈️ Ticketing Daily Entry"
# Use Streamlit Cloud Secrets first, then env var
DATABASE_URL = None
if hasattr(st, "secrets") and "DATABASE_URL" in st.secrets:
    DATABASE_URL = st.secrets["DATABASE_URL"]
DATABASE_URL = DATABASE_URL or os.getenv("DATABASE_URL")

# -------------------- DB HELPERS --------------------
def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it in Streamlit secrets.")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
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
            CREATE TABLE IF NOT EXISTS tickets (
                id SERIAL PRIMARY KEY,
                staff_user_id INTEGER NOT NULL REFERENCES users(id),

                travel_date DATE NOT NULL,
                ai_code TEXT NOT NULL,
                ticket_number TEXT NOT NULL,
                passenger_name TEXT NOT NULL,
                route TEXT,
                supplier TEXT,

                txn_type TEXT NOT NULL CHECK(txn_type IN ('SALE','REFUND')),
                basic_fare NUMERIC(14,2) NOT NULL DEFAULT 0,
                comm NUMERIC(14,2) NOT NULL DEFAULT 0,
                net_to_supp NUMERIC(14,2) NOT NULL DEFAULT 0,
                bill_to_customer NUMERIC(14,2) NOT NULL DEFAULT 0,

                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """)

def users_exist():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users;")
            return cur.fetchone()[0] > 0

def create_user(username: str, password: str, role: str, staff_name: str):
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users(username, password_hash, role, staff_name) VALUES (%s,%s,%s,%s)",
                (username.strip(), pw_hash, role, staff_name.strip())
            )

def verify_login(username: str, password: str):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, username, password_hash, role, staff_name, active
                FROM users
                WHERE username=%s
            """, (username.strip(),))
            row = cur.fetchone()

    if not row:
        return None
    if not row["active"]:
        return None

    if bcrypt.checkpw(password.encode("utf-8"), bytes(row["password_hash"])):
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "staff_name": row["staff_name"]
        }
    return None

def set_user_active(user_id: int, active: bool):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET active=%s WHERE id=%s", (active, user_id))

def list_users():
    with get_conn() as conn:
        return pd.read_sql("""
            SELECT id, username, role, staff_name, active, created_at
            FROM users
            ORDER BY role, staff_name
        """, conn)

def add_ticket(
    staff_id: int,
    travel_date,
    ai_code: str,
    ticket_number: str,
    passenger_name: str,
    route: str,
    supplier: str,
    txn_type: str,
    basic_fare: float,
    comm: float,
    net_to_supp: float,
    bill_to_customer: float
):
    # If refund, store as negative values (easy totals)
    if txn_type == "REFUND":
        basic_fare *= -1
        comm *= -1
        net_to_supp *= -1
        bill_to_customer *= -1

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tickets(
                    staff_user_id, travel_date, ai_code, ticket_number, passenger_name, route, supplier,
                    txn_type, basic_fare, comm, net_to_supp, bill_to_customer
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                staff_id, travel_date, ai_code.strip(), ticket_number.strip(), passenger_name.strip(),
                (route or "").strip(), (supplier or "").strip(),
                txn_type, basic_fare, comm, net_to_supp, bill_to_customer
            ))

def staff_tickets_df(staff_id: int, start_d=None, end_d=None):
    q = """
        SELECT travel_date AS "Date",
               ai_code AS "AI Code",
               ticket_number AS "Ticket Number",
               passenger_name AS "Passenger Name",
               route AS "Route",
               supplier AS "Supplier",
               txn_type AS "Type",
               basic_fare AS "Basic Fare",
               comm AS "Comm",
               net_to_supp AS "Net to supp",
               bill_to_customer AS "Bill to Customer",
               created_at
        FROM tickets
        WHERE staff_user_id = %s
    """
    params = [staff_id]
    if start_d and end_d:
        q += " AND travel_date BETWEEN %s AND %s"
        params += [start_d, end_d]
    q += " ORDER BY travel_date DESC, id DESC"

    with get_conn() as conn:
        return pd.read_sql(q, conn, params=tuple(params))

def all_tickets_df(start_d=None, end_d=None, staff_name=None):
    q = """
        SELECT t.travel_date AS "Date",
               u.staff_name AS "Staff",
               t.ai_code AS "AI Code",
               t.ticket_number AS "Ticket Number",
               t.passenger_name AS "Passenger Name",
               t.route AS "Route",
               t.supplier AS "Supplier",
               t.txn_type AS "Type",
               t.basic_fare AS "Basic Fare",
               t.comm AS "Comm",
               t.net_to_supp AS "Net to supp",
               t.bill_to_customer AS "Bill to Customer",
               t.created_at
        FROM tickets t
        JOIN users u ON u.id = t.staff_user_id
        WHERE 1=1
    """
    params = []
    if start_d and end_d:
        q += " AND t.travel_date BETWEEN %s AND %s"
        params += [start_d, end_d]
    if staff_name and staff_name.strip():
        q += " AND LOWER(u.staff_name) LIKE LOWER(%s)"
        params += [f"%{staff_name.strip()}%"]
    q += " ORDER BY t.travel_date DESC, t.id DESC"

    with get_conn() as conn:
        return pd.read_sql(q, conn, params=tuple(params))

# -------------------- UI --------------------
st.set_page_config(page_title="Ticketing App", layout="wide")
st.title(APP_TITLE)

# Safety: show clear message if DB not set
if not DATABASE_URL:
    st.error("DATABASE_URL is missing. Add it in Streamlit Cloud secrets (Settings → Secrets).")
    st.stop()

# Init DB
init_db()

if "user" not in st.session_state:
    st.session_state.user = None

# First-time admin
if not users_exist():
    st.warning("First time setup: Create ADMIN account")
    with st.form("create_admin"):
        a_user = st.text_input("Admin Username")
        a_name = st.text_input("Admin Name")
        a_pass = st.text_input("Admin Password", type="password")
        a_pass2 = st.text_input("Confirm Password", type="password")
        ok = st.form_submit_button("Create Admin")
    if ok:
        if not a_user or not a_name or not a_pass:
            st.error("Fill all fields.")
        elif a_pass != a_pass2:
            st.error("Passwords do not match.")
        else:
            try:
                create_user(a_user, a_pass, "admin", a_name)
                st.success("Admin created ✅ Now refresh and login.")
            except Exception as e:
                st.error(f"Error: {e}")
    st.stop()

# Login
if st.session_state.user is None:
    st.subheader("Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        user = verify_login(u, p)
        if user:
            st.session_state.user = user
            st.rerun()
        else:
            st.error("Invalid login or user inactive.")
    st.stop()

user = st.session_state.user

top1, top2 = st.columns([4,1])
with top1:
    st.caption(f"Logged in as **{user['staff_name']}** ({user['role']})")
with top2:
    if st.button("Logout"):
        st.session_state.user = None
        st.rerun()

# -------------------- STAFF PAGE --------------------
if user["role"] == "staff":
    left, right = st.columns([1, 2])

    with left:
        st.markdown("### Add Ticket Entry")
        with st.form("ticket_form"):
            travel_date = st.date_input("Date", value=date.today())
            txn_type = st.selectbox("Type", ["SALE", "REFUND"])
            ai_code = st.text_input("AI Code *")
            ticket_number = st.text_input("Ticket Number *")
            passenger_name = st.text_input("Passenger Name *")
            route = st.text_input("Route")
            supplier = st.text_input("Supplier")

            basic_fare = st.number_input("Basic Fare", min_value=0.0, step=10.0)
            comm = st.number_input("Comm", min_value=0.0, step=1.0)
            net_to_supp = st.number_input("Net to supp", min_value=0.0, step=10.0)
            bill_to_customer = st.number_input("Bill to Customer", min_value=0.0, step=10.0)

            save = st.form_submit_button("Save")

        if save:
            if not ai_code.strip() or not ticket_number.strip() or not passenger_name.strip():
                st.error("AI Code, Ticket Number, Passenger Name are required.")
            else:
                add_ticket(
                    user["id"], travel_date, ai_code, ticket_number, passenger_name,
                    route, supplier, txn_type,
                    basic_fare, comm, net_to_supp, bill_to_customer
                )
                st.success("Saved ✅")
                st.rerun()

        st.info("Refunds are saved as negative amounts automatically (so totals become correct).")

    with right:
        st.markdown("### Your Tickets")
        f1, f2 = st.columns(2)
        with f1:
            start = st.date_input("From", value=date.today().replace(day=1), key="s_from")
        with f2:
            end = st.date_input("To", value=date.today(), key="s_to")

        df = staff_tickets_df(user["id"], start, end)
        st.dataframe(df, use_container_width=True)

        if not df.empty:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Basic Fare", f"{df['Basic Fare'].sum():,.2f}")
            k2.metric("Comm", f"{df['Comm'].sum():,.2f}")
            k3.metric("Net to supp", f"{df['Net to supp'].sum():,.2f}")
            k4.metric("Bill to Customer", f"{df['Bill to Customer'].sum():,.2f}")

            export = df.drop(columns=["created_at"], errors="ignore")
            csv_bytes = export.to_csv(index=False).encode("utf-8")
            st.download_button("Download My CSV", data=csv_bytes, file_name="my_ticketing.csv", mime="text/csv")

# -------------------- ADMIN PAGE --------------------
else:
    st.markdown("## Admin Dashboard")

    tab1, tab2 = st.tabs(["All Tickets", "Users"])

    with tab1:
        f1, f2, f3 = st.columns(3)
        with f1:
            staff_filter = st.text_input("Filter by Staff (optional)")
        with f2:
            start = st.date_input("From", value=date.today().replace(day=1), key="a_from")
        with f3:
            end = st.date_input("To", value=date.today(), key="a_to")

        df = all_tickets_df(start, end, staff_filter)
        st.dataframe(df, use_container_width=True)

        if not df.empty:
            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Basic Fare", f"{df['Basic Fare'].sum():,.2f}")
            a2.metric("Comm", f"{df['Comm'].sum():,.2f}")
            a3.metric("Net to supp", f"{df['Net to supp'].sum():,.2f}")
            a4.metric("Bill to Customer", f"{df['Bill to Customer'].sum():,.2f}")

            export = df.drop(columns=["created_at"], errors="ignore")
            csv_bytes = export.to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV (Filtered)", data=csv_bytes,
                               file_name="all_ticketing_filtered.csv", mime="text/csv")

    with tab2:
        st.markdown("### Create Staff User")
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

        st.markdown("### Users List")
        users_df = list_users()
        st.dataframe(users_df, use_container_width=True)

        st.markdown("### Activate / Deactivate User")
        if not users_df.empty:
            uid = st.selectbox("Select User ID", users_df["id"].tolist())
            active = st.selectbox("Set Status", [True, False], format_func=lambda x: "Active" if x else "Inactive")
            if st.button("Update Status"):
                set_user_active(int(uid), bool(active))
                st.success("Updated ✅")
                st.rerun()
