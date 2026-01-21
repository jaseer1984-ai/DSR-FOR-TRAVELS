import streamlit as st
import pandas as pd
import gspread
import bcrypt
from datetime import datetime, date
from google.oauth2.service_account import Credentials

# ===================== UI CONFIG =====================
APP_TITLE = "✈️ Travel DSR"
st.set_page_config(page_title="Travel DSR", layout="wide")

CUSTOM_CSS = """
<style>
.main { background: linear-gradient(180deg, #f7f9ff 0%, #ffffff 55%, #ffffff 100%); }
.dsr-header {
  padding: 18px 18px;
  border-radius: 16px;
  background: linear-gradient(90deg, #3b82f6 0%, #8b5cf6 40%, #ec4899 100%);
  color: white;
  box-shadow: 0 10px 25px rgba(0,0,0,0.06);
  margin-bottom: 16px;
}
.small-muted { color: rgba(255,255,255,0.85); font-size: 14px; }
.card {
  background: #ffffff;
  border: 1px solid rgba(15,23,42,0.08);
  border-radius: 16px;
  padding: 14px 14px;
  box-shadow: 0 10px 25px rgba(0,0,0,0.04);
}
.login-wrap { max-width: 520px; margin: 0 auto; }
div.stButton > button { border-radius: 12px; font-weight: 600; padding: 10px 14px; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

st.markdown(
    f"""
    <div class="dsr-header">
      <div style="font-size:24px; font-weight:800;">{APP_TITLE}</div>
      <div class="small-muted">Google Sheet backend • Staff access • Admin dashboard</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ===================== SECRETS CHECK =====================
def need_secret(key: str):
    if key not in st.secrets or not str(st.secrets[key]).strip():
        st.error(f"Missing secret: {key}")
        st.stop()

need_secret("SHEET_ID")
need_secret("gcp_service_account")

ENTRIES_SHEET = st.secrets.get("ENTRIES_SHEET", "Entries")
USERS_SHEET = st.secrets.get("USERS_SHEET", "Users")

# ===================== GOOGLE SHEETS CONNECT =====================
@st.cache_resource
def get_gspread_client():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(creds)

def open_spreadsheet():
    gc = get_gspread_client()
    return gc.open_by_key(st.secrets["SHEET_ID"])

def get_or_create_worksheet(sh, title: str, rows: int = 2000, cols: int = 30):
    try:
        return sh.worksheet(title)
    except Exception:
        return sh.add_worksheet(title=title, rows=str(rows), cols=str(cols))

def ensure_headers(ws, headers: list[str]):
    existing = ws.row_values(1)

    if not existing or all(str(x).strip() == "" for x in existing):
        ws.update("A1", [headers])
        return

    if len(existing) < len(headers):
        existing = existing + [""] * (len(headers) - len(existing))

    changed = False
    for i, h in enumerate(headers):
        if str(existing[i]).strip() == "":
            existing[i] = h
            changed = True

    if changed:
        ws.update("A1", [existing[:len(headers)]])

def init_sheets():
    sh = open_spreadsheet()

    ws_users = get_or_create_worksheet(sh, USERS_SHEET, rows=2000, cols=10)
    ws_entries = get_or_create_worksheet(sh, ENTRIES_SHEET, rows=5000, cols=25)

    users_headers = ["username", "password_hash", "role", "staff_name", "active", "created_at"]

    entries_headers = [
        "Date", "Staff", "Entry Type", "AI Code", "Ticket Number", "Passenger Name",
        "Route",
        "Base Fare", "Tax", "Comm", "SC Supp", "VAT",
        "Net to Supplier", "To Collect from Customer",
        "Supplier", "Ref No",
        "Receipt", "ADM",
        "Notes", "Created At"
    ]

    ensure_headers(ws_users, users_headers)
    ensure_headers(ws_entries, entries_headers)

    return ws_users, ws_entries

# ===================== USERS / AUTH =====================
def users_df(ws_users) -> pd.DataFrame:
    data = ws_users.get_all_records()
    df = pd.DataFrame(data)
    if df.empty:
        return pd.DataFrame(columns=["username","password_hash","role","staff_name","active","created_at"])

    for c in ["username","password_hash","role","staff_name"]:
        if c in df.columns:
            df[c] = df[c].astype(str)

    if "active" in df.columns:
        df["active"] = df["active"].astype(str).str.lower().isin(["true","1","yes","y"])
    else:
        df["active"] = True

    return df

def hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def check_pw(password: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except Exception:
        return False

def find_user(df: pd.DataFrame, username: str):
    username = username.strip().lower()
    if df.empty:
        return None
    m = df["username"].astype(str).str.lower() == username
    if m.any():
        return df[m].iloc[0].to_dict()
    return None

def add_user(ws_users, username: str, password: str, role: str, staff_name: str, active: bool=True):
    username = username.strip()
    staff_name = staff_name.strip()
    ph = hash_pw(password)
    ws_users.append_row([
        username,
        ph,
        role,
        staff_name,
        "TRUE" if active else "FALSE",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ], value_input_option="USER_ENTERED")

def set_user_active(ws_users, username: str, active: bool):
    records = ws_users.get_all_records()
    for i, r in enumerate(records, start=2):
        if str(r.get("username","")).strip().lower() == username.strip().lower():
            ws_users.update(f"E{i}", "TRUE" if active else "FALSE")
            return True
    return False

def reset_user_password(ws_users, username: str, new_password: str):
    records = ws_users.get_all_records()
    for i, r in enumerate(records, start=2):
        if str(r.get("username","")).strip().lower() == username.strip().lower():
            ws_users.update(f"B{i}", hash_pw(new_password))
            return True
    return False

def ensure_admin(ws_users):
    df = users_df(ws_users)
    if not df.empty:
        return

    admin_u = str(st.secrets.get("ADMIN_USERNAME", "")).strip()
    admin_p = str(st.secrets.get("ADMIN_PASSWORD", "")).strip()
    admin_n = str(st.secrets.get("ADMIN_NAME", "Admin")).strip()

    if admin_u and admin_p:
        add_user(ws_users, admin_u, admin_p, "admin", admin_n, True)
        st.toast("Admin created from secrets ✅", icon="✅")
    else:
        st.warning("No users found. Add ADMIN_USERNAME / ADMIN_PASSWORD in Secrets to auto-create admin.")
        st.stop()

# ===================== ENTRIES =====================
NUM_COLS = [
    "Base Fare", "Tax", "Comm", "SC Supp", "VAT",
    "Net to Supplier", "To Collect from Customer",
    "Receipt", "ADM"
]

def entries_df(ws_entries) -> pd.DataFrame:
    data = ws_entries.get_all_records()
    df = pd.DataFrame(data)
    if df.empty:
        return pd.DataFrame(columns=[
            "Date","Staff","Entry Type","AI Code","Ticket Number","Passenger Name",
            "Route",
            "Base Fare","Tax","Comm","SC Supp","VAT",
            "Net to Supplier","To Collect from Customer",
            "Supplier","Ref No",
            "Receipt","ADM",
            "Notes","Created At"
        ])

    for col in NUM_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date

    return df

def append_entry(ws_entries, row: dict):
    out = [
        row.get("Date",""),
        row.get("Staff",""),
        row.get("Entry Type",""),
        row.get("AI Code",""),
        row.get("Ticket Number",""),
        row.get("Passenger Name",""),
        row.get("Route",""),

        row.get("Base Fare",0),
        row.get("Tax",0),
        row.get("Comm",0),
        row.get("SC Supp",0),
        row.get("VAT",0),

        row.get("Net to Supplier",0),
        row.get("To Collect from Customer",0),

        row.get("Supplier",""),
        row.get("Ref No",""),

        row.get("Receipt",0),
        row.get("ADM",0),

        row.get("Notes",""),
        row.get("Created At",""),
    ]
    ws_entries.append_row(out, value_input_option="USER_ENTERED")

def calc_outstanding(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    return float(df.get("To Collect from Customer", 0).sum() + df.get("ADM", 0).sum() - df.get("Receipt", 0).sum())

# ===================== INIT =====================
ws_users, ws_entries = init_sheets()
ensure_admin(ws_users)

if "user" not in st.session_state:
    st.session_state.user = None

# ===================== LOGIN UI =====================
def login_view():
    st.markdown('<div class="login-wrap">', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("🔐 Login")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login")

    if ok:
        dfu = users_df(ws_users)
        u = find_user(dfu, username)
        if not u:
            st.error("Invalid login.")
        elif not bool(u.get("active", True)):
            st.error("User inactive.")
        elif not check_pw(password, str(u.get("password_hash",""))):
            st.error("Invalid login.")
        else:
            st.session_state.user = {"username": u["username"], "role": u["role"], "staff_name": u["staff_name"]}
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ===================== STAFF VIEW =====================
def staff_view(user):
    st.markdown(f"**Logged in:** {user['staff_name']}  •  Role: `{user['role']}`")

    df = entries_df(ws_entries)
    mydf = df[df["Staff"].astype(str).str.lower() == user["staff_name"].strip().lower()].copy()

    # Filters top
    c1, c2, c3 = st.columns([1,1,2])
    with c1:
        start = st.date_input("From", value=date.today().replace(day=1), key="staff_from")
    with c2:
        end = st.date_input("To", value=date.today(), key="staff_to")
    with c3:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        out_val = calc_outstanding(mydf[(mydf["Date"]>=start) & (mydf["Date"]<=end)])
        st.metric("📌 Outstanding (Your)", f"{out_val:,.2f}")
        st.markdown("</div>", unsafe_allow_html=True)

    left, right = st.columns([1.1, 2.2], gap="large")

    # ----- ADD ENTRY -----
    with left:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### ➕ Add Entry")

        with st.form("entry_form"):
            entry_date = st.date_input("Date", value=date.today())
            entry_type = st.selectbox("Entry Type", ["SALE", "REFUND", "RECEIPT", "ADM"])
            notes = st.text_input("Notes (optional)")

            # defaults
            ai_code = ticket_no = pax = route = supplier = ref_no = ""
            base_fare = tax = comm = sc_supp = 0.0
            vat = net_to_supplier = to_collect = 0.0
            receipt = adm = 0.0

            if entry_type in ["SALE", "REFUND"]:
                ai_code = st.text_input("AI Code *")
                ticket_no = st.text_input("Ticket Number *")
                pax = st.text_input("Passenger Name *")
                route = st.text_input("Route")
                supplier = st.text_input("Supplier")
                ref_no = st.text_input("Ref No (optional)")

                base_fare = st.number_input("Base Fare", min_value=0.0, step=10.0)
                tax = st.number_input("Tax", min_value=0.0, step=10.0)
                comm = st.number_input("Comm", min_value=0.0, step=10.0)
                sc_supp = st.number_input("SC Supp", min_value=0.0, step=10.0)

                vat = round(sc_supp * 0.15, 2)
                net_to_supplier = round(base_fare + tax - comm + sc_supp + vat, 2)
                to_collect = net_to_supplier

                st.caption(f"VAT (15% of SC Supp): {vat:,.2f}")
                st.caption(f"Net to Supplier: {net_to_supplier:,.2f}")
                st.caption(f"To Collect from Customer: {to_collect:,.2f}")

            elif entry_type == "RECEIPT":
                supplier = st.text_input("Supplier (optional)")
                ref_no = st.text_input("Receipt Ref No *")
                receipt = st.number_input("Receipt Amount", min_value=0.0, step=10.0)
                ticket_no = st.text_input("Ticket Number (optional)")
                pax = st.text_input("Passenger Name (optional)")
                st.caption(f"Receipt will reduce Outstanding by: {receipt:,.2f}")

            elif entry_type == "ADM":
                supplier = st.text_input("Supplier (optional)")
                ref_no = st.text_input("ADM Ref No *")
                adm = st.number_input("ADM Amount", min_value=0.0, step=10.0)
                ticket_no = st.text_input("Ticket Number (optional)")
                pax = st.text_input("Passenger Name (optional)")
                st.caption(f"ADM will increase Outstanding by: {adm:,.2f}")

            save = st.form_submit_button("✅ Save Entry")

        if save:
            # Validation
            if entry_type in ["SALE","REFUND"]:
                if not ai_code.strip() or not ticket_no.strip() or not pax.strip():
                    st.error("AI Code, Ticket Number, Passenger Name are required.")
                    st.stop()

            if entry_type in ["RECEIPT","ADM"] and not ref_no.strip():
                st.error("Ref No is required.")
                st.stop()

            # Build row
            if entry_type in ["SALE", "REFUND"]:
                sign = -1 if entry_type == "REFUND" else 1

                row = {
                    "Date": entry_date.strftime("%Y-%m-%d"),
                    "Staff": user["staff_name"],
                    "Entry Type": entry_type,
                    "AI Code": ai_code,
                    "Ticket Number": ticket_no,
                    "Passenger Name": pax,
                    "Route": route,

                    "Base Fare": sign * float(base_fare),
                    "Tax": sign * float(tax),
                    "Comm": sign * float(comm),
                    "SC Supp": sign * float(sc_supp),
                    "VAT": sign * float(vat),

                    "Net to Supplier": sign * float(net_to_supplier),
                    "To Collect from Customer": sign * float(to_collect),

                    "Supplier": supplier,
                    "Ref No": ref_no,

                    "Receipt": 0.0,
                    "ADM": 0.0,

                    "Notes": notes,
                    "Created At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

            elif entry_type == "RECEIPT":
                row = {
                    "Date": entry_date.strftime("%Y-%m-%d"),
                    "Staff": user["staff_name"],
                    "Entry Type": entry_type,
                    "AI Code": "",
                    "Ticket Number": ticket_no,
                    "Passenger Name": pax,
                    "Route": "",

                    "Base Fare": 0.0,
                    "Tax": 0.0,
                    "Comm": 0.0,
                    "SC Supp": 0.0,
                    "VAT": 0.0,
                    "Net to Supplier": 0.0,
                    "To Collect from Customer": 0.0,

                    "Supplier": supplier,
                    "Ref No": ref_no,

                    "Receipt": float(receipt),
                    "ADM": 0.0,

                    "Notes": notes,
                    "Created At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

            else:  # ADM
                row = {
                    "Date": entry_date.strftime("%Y-%m-%d"),
                    "Staff": user["staff_name"],
                    "Entry Type": entry_type,
                    "AI Code": "",
                    "Ticket Number": ticket_no,
                    "Passenger Name": pax,
                    "Route": "",

                    "Base Fare": 0.0,
                    "Tax": 0.0,
                    "Comm": 0.0,
                    "SC Supp": 0.0,
                    "VAT": 0.0,
                    "Net to Supplier": 0.0,
                    "To Collect from Customer": 0.0,

                    "Supplier": supplier,
                    "Ref No": ref_no,

                    "Receipt": 0.0,
                    "ADM": float(adm),

                    "Notes": notes,
                    "Created At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

            try:
                append_entry(ws_entries, row)
                st.success("Saved ✅")
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")
                st.stop()

        st.info("Refund auto-reverses Sale amounts. Receipt reduces Outstanding. ADM increases Outstanding.")
        st.markdown("</div>", unsafe_allow_html=True)

    # ----- YOUR ENTRIES TABLE -----
    with right:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### 📄 Your Entries")

        show = mydf.copy()
        show = show[(show["Date"] >= start) & (show["Date"] <= end)]
        show = show.sort_values(by=["Date", "Created At"], ascending=[False, False])

        st.dataframe(show, use_container_width=True, hide_index=True)

        if not show.empty:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("To Collect", f"{show['To Collect from Customer'].sum():,.2f}")
            k2.metric("Receipt", f"{show['Receipt'].sum():,.2f}")
            k3.metric("ADM", f"{show['ADM'].sum():,.2f}")
            k4.metric("Outstanding", f"{calc_outstanding(show):,.2f}")

            st.download_button(
                "⬇️ Download CSV (My Filtered)",
                data=show.to_csv(index=False).encode("utf-8"),
                file_name="my_dsr.csv",
                mime="text/csv"
            )
        st.markdown("</div>", unsafe_allow_html=True)

# ===================== ADMIN VIEW =====================
def admin_view(user):
    st.markdown(f"**Logged in:** {user['staff_name']}  •  Role: `{user['role']}`")

    tabs = st.tabs(["📊 All Entries", "👥 Users", "📌 Outstanding by Staff"])

    with tabs[0]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        df = entries_df(ws_entries)

        c1, c2, c3 = st.columns([1.2, 1, 1])
        with c1:
            staff_filter = st.text_input("Filter by Staff (optional)")
        with c2:
            start = st.date_input("From", value=date.today().replace(day=1), key="adm_from")
        with c3:
            end = st.date_input("To", value=date.today(), key="adm_to")

        show = df.copy()
        show = show[(show["Date"] >= start) & (show["Date"] <= end)]
        if staff_filter.strip():
            show = show[show["Staff"].astype(str).str.lower().str.contains(staff_filter.strip().lower())]

        show = show.sort_values(by=["Date", "Created At"], ascending=[False, False])

        st.dataframe(show, use_container_width=True, hide_index=True)

        if not show.empty:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("To Collect", f"{show['To Collect from Customer'].sum():,.2f}")
            k2.metric("Receipt", f"{show['Receipt'].sum():,.2f}")
            k3.metric("ADM", f"{show['ADM'].sum():,.2f}")
            k4.metric("Outstanding", f"{calc_outstanding(show):,.2f}")

            st.download_button(
                "⬇️ Download CSV (Filtered)",
                data=show.to_csv(index=False).encode("utf-8"),
                file_name="all_dsr_filtered.csv",
                mime="text/csv"
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with tabs[1]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### ➕ Create Staff User")

        with st.form("create_staff"):
            nu = st.text_input("Username (login)")
            nn = st.text_input("Staff Name")
            np = st.text_input("Password", type="password")
            create = st.form_submit_button("Create Staff")

        if create:
            dfu = users_df(ws_users)
            if not nu.strip() or not nn.strip() or not np.strip():
                st.error("Fill all fields.")
            elif find_user(dfu, nu):
                st.error("Username already exists.")
            else:
                add_user(ws_users, nu, np, "staff", nn, True)
                st.success("Staff created ✅")
                st.rerun()

        st.markdown("---")
        st.markdown("### 👥 Users List")
        dfu = users_df(ws_users)
        df_show = dfu.drop(columns=["password_hash"], errors="ignore")
        st.dataframe(df_show, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### ✅ Activate / Deactivate")
        if not dfu.empty:
            sel_user = st.selectbox("Select username", dfu["username"].tolist())
            new_status = st.selectbox("Status", [True, False], format_func=lambda x: "Active" if x else "Inactive")
            if st.button("Update Status"):
                set_user_active(ws_users, sel_user, bool(new_status))
                st.success("Updated ✅")
                st.rerun()

        st.markdown("---")
        st.markdown("### 🔑 Reset Password")
        if not dfu.empty:
            sel_user2 = st.selectbox("User", dfu["username"].tolist(), key="reset_user")
            new_pw = st.text_input("New Password", type="password")
            if st.button("Reset Password"):
                if not new_pw.strip():
                    st.error("Enter new password")
                else:
                    reset_user_password(ws_users, sel_user2, new_pw)
                    st.success("Password updated ✅")
                    st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    with tabs[2]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        df = entries_df(ws_entries)

        if df.empty:
            st.info("No data yet.")
        else:
            grp = df.groupby("Staff", as_index=False).agg({
                "To Collect from Customer": "sum",
                "Receipt": "sum",
                "ADM": "sum"
            })
            grp["Outstanding"] = grp["To Collect from Customer"] + grp["ADM"] - grp["Receipt"]
            grp = grp.sort_values("Outstanding", ascending=False)

            st.dataframe(grp, use_container_width=True, hide_index=True)

            st.download_button(
                "⬇️ Download Outstanding Summary",
                data=grp.to_csv(index=False).encode("utf-8"),
                file_name="outstanding_summary.csv",
                mime="text/csv"
            )
        st.markdown("</div>", unsafe_allow_html=True)

# ===================== LOGOUT BUTTON =====================
def topbar(user):
    c1, c2 = st.columns([4,1])
    with c2:
        if st.button("Logout"):
            st.session_state.user = None
            st.rerun()

# ===================== ROUTER =====================
if st.session_state.user is None:
    login_view()
else:
    user = st.session_state.user
    topbar(user)

    if user["role"] == "admin":
        admin_view(user)
    else:
        staff_view(user)
