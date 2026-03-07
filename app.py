
import streamlit as st
import pandas as pd
import datetime
import io
import urllib.request
from fpdf import FPDF
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# Helper to load creds from Streamlit Secrets
def get_gcp_creds():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    # Try local file first (for local dev)
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            r"C:\Users\vizal\Cx360\cx360-447406-93f667785dd1.json", scope)
        return creds
    except Exception:
        pass
        
    # Fallback to Streamlit Secrets (for Cloud Deployment)
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return creds
        
    raise ValueError("Google Service Account credentials not found in local file or secrets.")

# --- CONFIGURATION ---
st.set_page_config(page_title="Invoice Generator", page_icon="🧾", layout="wide")


# --- APP START ---
logo_url = "https://lilcoo.in/wp-content/uploads/2026/02/LilCoo-Logo.png"
# --- MOCK DATA ---
# This is now fetched from Google Sheets below.

# --- PRELOAD GOOGLE SHEETS DATA ---
@st.cache_data(ttl=600)
def get_google_sheets_data():
    data = {
        'billed_by': {},
        'clients': {},
        'products': {
            "Select Product": {"hsn": "", "price": 0, "gst": 18, "name": "Select Product"}
        }
    }
    try:
        creds = get_gcp_creds()
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key('1msnl_ZYZTvl1j45mjPI9FvzXDphJNLsOPfhyNxanK5I')
        
        # Billed By
        try:
            billed_by_records = spreadsheet.worksheet('Billed By').get_all_records()
            if billed_by_records:
                data['billed_by'] = billed_by_records[0]
        except Exception as e:
            print(f"Error fetching Billed By: {e}")
            
        # Clients
        try:
            clients_records = spreadsheet.worksheet('Clients').get_all_records()
            if clients_records:
                data['clients'] = {}
                for row in clients_records:
                    if row.get('Client Name'):
                        key = f"{row.get('Client Name')} - {row.get('State', 'Unknown')}"
                        data['clients'][key] = {
                            "name": row.get('Client Name', ''),
                            "address": row.get('Address', ''),
                            "state": row.get('State', ''),
                            "gstin": str(row.get('GSTIN', '')),
                            "pan": str(row.get('PAN', '')),
                            "phone": str(row.get('Phone', ''))
                        }
        except Exception as e:
            print(f"Error fetching Clients: {e}")
            
        # Products
        try:
            products_sheet = spreadsheet.worksheet('Products')
            all_values = products_sheet.get_all_values()
            
            if len(all_values) > 1:
                headers = all_values[0]
                products_records = [dict(zip(headers, row)) for row in all_values[1:]]
            else:
                products_records = []

            if products_records:
                data['products'] = {"Select Product": {"hsn": "", "price": 0, "mrp": 0, "gst": 18, "name": "Select Product"}}
                for row in products_records:
                    if row.get('Product Name') and str(row.get('Product Name')).strip():
                        # Extract price carefully
                        try:
                            price_raw = str(row.get('Price', 0))
                            if not price_raw.strip(): price_raw = "0"
                            price_val = float(price_raw.replace(',', ''))
                        except Exception:
                            price_val = 0.0
                            
                        # Extract MRP carefully, defaulting to price if missing
                        try:
                            mrp_raw = str(row.get('MRP', ''))
                            if mrp_raw.strip():
                                mrp_val = float(mrp_raw.replace(',', ''))
                            else:
                                mrp_val = price_val
                        except Exception:
                            mrp_val = price_val
                            
                        # Extract GST carefully
                        try:
                            gst_raw = str(row.get('GST %', 18))
                            if not gst_raw.strip(): gst_raw = "18"
                            gst_val = int(gst_raw.replace('%', ''))
                        except Exception:
                            gst_val = 18
                            
                        data['products'][row.get('Product Name')] = {
                            "name": row.get('Product Name', ''),
                            "hsn": str(row.get('HSN Code', '')),
                            "price": price_val,
                            "mrp": mrp_val,
                            "gst": gst_val
                        }
        except Exception as e:
            print(f"Error fetching Products: {e}")
            
    except Exception as e:
        print(f"Error loading data from Google Sheets: {e}")
        st.error(f"⚠️ Could not load data from Google Sheets. Check your Secrets/Credentials.")
    return data

gs_data = get_google_sheets_data()
billed_by = gs_data['billed_by']
MOCK_CLIENTS = gs_data['clients']
MOCK_PRODUCTS = gs_data['products']

# --- CALLBACK FOR DISCOUNT & PRODUCT SYNC ---
def on_discount_change():
    global_val = st.session_state.get("global_discount_input", 0.0)
    if 'item_rows' in st.session_state:
        for i in range(st.session_state.item_rows):
            if f"ind_discount_{i}" in st.session_state:
                st.session_state[f"ind_discount_{i}"] = global_val
            # Reset tracking so the loop knows it was forced to change
            st.session_state[f"last_discount_{i}"] = None

def on_product_change(idx):
    selected = st.session_state[f"prod_select_{idx}"]
    if selected in MOCK_PRODUCTS:
        st.session_state[f"prod_name_{idx}"] = MOCK_PRODUCTS[selected]["name"] if selected != "Select Product" else ""
        st.session_state[f"hsn_{idx}"] = MOCK_PRODUCTS[selected]["hsn"]
        st.session_state[f"gst_{idx}"] = int(MOCK_PRODUCTS[selected]["gst"])
        
        sheet_price = float(MOCK_PRODUCTS[selected]["price"])
        sheet_mrp = float(MOCK_PRODUCTS[selected].get("mrp", sheet_price))
        gst_percent = int(MOCK_PRODUCTS[selected]["gst"])
        
        st.session_state[f"original_sheet_price_{idx}"] = sheet_price
        st.session_state[f"mrp_{idx}"] = sheet_mrp
        st.session_state[f"ind_discount_{idx}"] = st.session_state.get("global_discount_input", 0.0)
        
        initial_rate = sheet_price / (1.0 + (gst_percent / 100.0))
        st.session_state[f"price_{idx}"] = round(initial_rate, 2)
        
def on_disc_change(idx):
    # When discount changes, recalculate price
    original = st.session_state.get(f"original_sheet_price_{idx}", 0.0)
    current_gst = st.session_state.get(f"gst_{idx}", 18)
    disc = st.session_state.get(f"ind_discount_{idx}", 0.0)
    if original > 0:
        disc_sheet_price = original * ((100.0 - disc) / 100.0)
        new_rate = disc_sheet_price / (1.0 + (current_gst / 100.0))
        st.session_state[f"price_{idx}"] = round(new_rate, 2)

def on_price_change(idx):
    # When price changes, recalculate discount
    original = st.session_state.get(f"original_sheet_price_{idx}", 0.0)
    current_gst = st.session_state.get(f"gst_{idx}", 18)
    new_price = st.session_state.get(f"price_{idx}", 0.0)
    
    if original > 0:
        # Reconstruct the price including GST (what it would be on the sheet)
        implied_sheet_price = new_price * (1.0 + (current_gst / 100.0))
        
        # Calculate what percentage this is of the original price
        ratio = implied_sheet_price / original
        calc_disc = 100.0 - (ratio * 100.0)
        
        # Cap limits
        if calc_disc < 0: calc_disc = 0.0
        if calc_disc > 100.0: calc_disc = 100.0
        
        st.session_state[f"ind_discount_{idx}"] = round(calc_disc, 2)


CLIENT_OPTIONS = ["Select Client", "Create New Client"] + list(MOCK_CLIENTS.keys())
STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", 
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", 
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", 
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", 
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", 
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry", "Other"
]

# --- APP HEADER / BILLED BY ---
head1, head2 = st.columns([1, 1])

# Calculate next invoice number automatically
# We use the length of the invoices sheet plus 1 as a rough auto-increment if no state exists
def get_next_invoice_number(current_count):
    # A=0, B=1 ... Z=25
    # Max per letter is 100,000 (00000 to 99999)
    letter_idx = current_count // 100000
    num_part = current_count % 100000
    if letter_idx > 25:
        # Loop back or handle AA, AB etc. For now just stick to A-Z
        letter_idx = 25
    letter = chr(65 + letter_idx)
    return f"{letter}{num_part:05d}"
    
def get_next_alpha_numeric(current_val):
    try:
        letter = current_val[0]
        num_part = int(current_val[1:])
        num_part += 1
        if num_part > 99999:
            num_part = 0
            if letter != 'Z':
                letter = chr(ord(letter) + 1)
        return f"{letter}{num_part:05d}"
    except Exception:
        return "A00001"

def fetch_latest_invoice_number_from_sheet():
    try:
        creds = get_gcp_creds()
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key('1msnl_ZYZTvl1j45mjPI9FvzXDphJNLsOPfhyNxanK5I')
        
        invoices_sheet = spreadsheet.worksheet('Invoices')
        records = invoices_sheet.get_all_records()
        
        if not records:
            return "A00001"
            
        last_invoice = records[-1].get('Invoice No', '')
        if not last_invoice:
            return "A00001"
            
        return get_next_alpha_numeric(last_invoice)
    except Exception as e:
        print(f"Error fetching latest invoice: {e}")
        return "A00001"

if 'invoice_num_override' not in st.session_state:
    st.session_state.invoice_num_override = fetch_latest_invoice_number_from_sheet()

with head1:
    st.subheader("Billed By")
    if billed_by.get('Company Name'):
        st.markdown(f"""**{billed_by.get('Company Name', '')}**  
{billed_by.get('Address Line 1', '')}  
{billed_by.get('Address Line 2', '')}  
**GSTIN:** {billed_by.get('GSTIN', '')}  
**PAN:** {billed_by.get('PAN', '')}  
**Phone:** {billed_by.get('Phone', '')}""")
    from_state = billed_by.get('State', 'Karnataka')

st.title("Invoice Generator")

with head2:
    st.image(logo_url, width=150)
    
st.divider()

# --- INVOICE DETAILS ---
col1, col2, col3 = st.columns(3)

with col1:
    invoice_number = st.text_input(
        "Invoice No", 
        value=st.session_state.invoice_num_override, 
        disabled=False, 
        key="invoice_no_input"
    )
with col2:
    invoice_date = st.date_input("Invoice Date", datetime.date.today(), key="invoice_date_input")
with col3:
    due_date = st.date_input("Due Date", datetime.date.today() + datetime.timedelta(days=7), key="invoice_due_date_input")

# --- BILLED TO ---
st.markdown("**Billed To**")
client_selection = st.selectbox("Select Existing Client", CLIENT_OPTIONS, label_visibility="collapsed", key="client_select_input")

if client_selection == "Create New Client":
    c_left, c_right = st.columns(2)
    with c_left:
        to_name = st.text_input("Company Name", key="to_name_input")
        to_address = st.text_area("Address", key="to_address_input")
        to_state = st.selectbox("State", STATES, index=0, key="to_state_input")
    with c_right:
        to_gstin = st.text_input("GSTIN", key="to_gstin_input")
        to_pan = st.text_input("PAN", key="to_pan_input")
        to_phone = st.text_input("Phone", key="to_phone_input")
elif client_selection in MOCK_CLIENTS:
    client = MOCK_CLIENTS[client_selection]
    st.write(f"**Company:** {client['name']}")
    st.write(f"**Address:** {client['address']}")
    st.write(f"**State:** {client['state']}")
    st.write(f"**GSTIN:** {client['gstin']}")
    st.write(f"**PAN:** {client['pan']}")
    if client['phone'] and str(client['phone']).strip() != "":
        st.write(f"**Phone:** {client['phone']}")
    # Set state variables
    to_name = client['name']
    to_address = client['address']
    to_state = client['state']
    to_gstin = client['gstin']
    to_pan = client['pan']
    to_phone = client['phone'] if client['phone'] and str(client['phone']).strip() != "" else ""
else:
    to_name = ""
    to_address = ""
    to_state = "Karnataka"
    to_gstin = ""
    to_pan = ""
    to_phone = ""

st.divider()

# --- DISCOUNT ---
col_d1, col_d2 = st.columns([1, 2])
with col_d1:
    st.number_input(
        "Discount %", 
        min_value=0.0, 
        max_value=100.0, 
        step=1.0, 
        value=0.0, 
        key="global_discount_input",
        on_change=on_discount_change
    )

st.divider()

# --- INVOICE ITEMS SECTION ---
st.subheader("Invoice Items")

if 'item_rows' not in st.session_state:
    st.session_state.item_rows = 1

def add_row():
    st.session_state.item_rows += 1

def remove_row():
    if st.session_state.item_rows > 1:
        st.session_state.item_rows -= 1

invoice_items = []

for i in range(st.session_state.item_rows):
    st.write(f"**Item {i+1}**")
    c1, c1b, c2, c2b, c3, c3b, c4, c5 = st.columns([1.5, 1.5, 0.8, 0.8, 0.7, 0.8, 1, 0.8])
    
    with c1:
        # User selects from dropdown
        selected_product = st.selectbox(f"Select Product", list(MOCK_PRODUCTS.keys()), key=f"prod_select_{i}", on_change=on_product_change, args=(i,))
        
    with c1b:
        # User can edit the name freely
        product_name = st.text_input(f"Item Name", key=f"prod_name_{i}")
    
    with c2:
        hsn_code = st.text_input("HSN", key=f"hsn_{i}")
    with c2b:
        if f"mrp_{i}" not in st.session_state:
            st.session_state[f"mrp_{i}"] = 0
            
        # Read the current float mrp from session state, display it as int via step/format
        # Streamlit number_input handles float -> int conversion for UI if step is int and format is %d
        current_mrp = int(st.session_state[f"mrp_{i}"])
        mrp_val = st.number_input("MRP", min_value=0, step=1, key=f"mrp_{i}", value=current_mrp)
    with c3:
        quantity = st.number_input("Qty", min_value=1, value=1, step=1, key=f"qty_{i}")
    with c3b:
        if f"ind_discount_{i}" not in st.session_state:
            st.session_state[f"ind_discount_{i}"] = st.session_state.get("global_discount_input", 0.0)
        ind_discount = st.number_input("Disc %", min_value=0.0, max_value=100.0, step=1.0, key=f"ind_discount_{i}", on_change=on_disc_change, args=(i,))
    with c4:
        if f"price_{i}" not in st.session_state:
            st.session_state[f"price_{i}"] = 0.0
            
        base_price = st.number_input("Unit Rate", min_value=0.0, step=100.0, key=f"price_{i}", on_change=on_price_change, args=(i,))
    with c5:
        if f"gst_{i}" not in st.session_state:
            st.session_state[f"gst_{i}"] = 18
        gst_percent = st.number_input("GST %", min_value=0, step=1, key=f"gst_{i}")
        
    row_total_base = base_price * quantity
    
    if from_state == "Karnataka" and to_state == "Karnataka":
        cgst_amt = (row_total_base * (gst_percent / 2.0)) / 100.0
        sgst_amt = (row_total_base * (gst_percent / 2.0)) / 100.0
        igst_amt = 0
    else:
        cgst_amt = 0
        sgst_amt = 0
        igst_amt = (row_total_base * gst_percent) / 100.0
        
    row_total_final = row_total_base + cgst_amt + sgst_amt + igst_amt
    
    if product_name.strip():
        invoice_items.append({
            "product": product_name,
            "hsn": hsn_code,
            "mrp": mrp_val,
            "disc_percent": float(ind_discount),
            "gst_percent": int(gst_percent),
            "qty": quantity,
            "price": base_price,
            "base_total": row_total_base,
            "cgst": cgst_amt,
            "sgst": sgst_amt,
            "igst": igst_amt,
            "total": row_total_final
        })
    st.write("---")

col_btn1, col_btn2 = st.columns(2)
with col_btn1:
    st.button("➕ Add Another Item", on_click=add_row)
with col_btn2:
    st.button("➖ Remove Last Item", on_click=remove_row)

st.divider()

# --- SUMMARY SECTION ---
st.subheader("Invoice Summary")

# Initialize totals and variables with defaults to avoid NameErrors
subtotal, total_cgst, total_sgst, total_igst, grand_total = 0.0, 0.0, 0.0, 0.0, 0.0
pdf_bytes = None
df = pd.DataFrame(invoice_items) if invoice_items else pd.DataFrame(columns=["product", "price", "qty", "base_total", "total", "cgst", "sgst", "igst"])

if invoice_items:
    subtotal = df["base_total"].sum()
    total_cgst = df["cgst"].sum()
    total_sgst = df["sgst"].sum()
    total_igst = df["igst"].sum()
    grand_total = df["total"].sum()
    
    # Simple display
    st.dataframe(df[["product", "price", "qty", "base_total", "total"]])
    
    col1_total, col2_total = st.columns([2, 1])
    with col2_total:
        st.write(f"**Subtotal:** ₹{subtotal:,.2f}")
        if from_state == "Karnataka" and to_state == "Karnataka":
            st.write(f"**CGST:** ₹{total_cgst:,.2f}")
            st.write(f"**SGST:** ₹{total_sgst:,.2f}")
        else:
            st.write(f"**IGST:** ₹{total_igst:,.2f}")
        st.markdown(f"### **Grand Total: ₹{grand_total:,.2f}**")
        
    st.divider()
    
    # PDF Generation Setup
    pdf_bytes = None
    try:
        def generate_pdf():
            # Helper to strip unsupported unicode characters before sending to fpdf2
            def clean_text(t):
                if t is None: return ""
                t = str(t)
                # Specific character replacements
                t = t.replace('\u20b9', 'Rs.').replace('\u2018', "'").replace('\u2019', "'")
                t = t.replace('\u201c', '"').replace('\u201d', '"').replace('\u2013', '-')
                t = t.replace('\u2014', '-').replace('\u00a0', ' ').replace('\u200b', '')
                
                # Strip bidirectional/isolate formatting characters that Helvetica doesn't support
                for char in ['\u2066', '\u2067', '\u2068', '\u2069', '\u200e', '\u200f', '\u202a', '\u202b', '\u202c', '\u202d', '\u202e']:
                    t = t.replace(char, '')
                    
                return t.encode('latin-1', 'replace').decode('latin-1')

            # We need a custom class to handle multi-page headers and footers properly
            class InvoicePDF(FPDF):
                def __init__(self, inv_no, inv_date, billed_to_name):
                    super().__init__()
                    self.inv_no = inv_no
                    self.inv_date = inv_date
                    self.billed_to_name = billed_to_name
                    
                def footer(self):
                    # Go to 35 mm from bottom
                    self.set_y(-35)
                    
                    # Separator Line First (The "Page Break" line)
                    self.line(self.get_x(), self.get_y(), 210 - self.get_x(), self.get_y())
                    self.ln(2)
                    
                    # --- Page Breaker Info ---
                    # Left side: Invoice No and Date
                    # Right side: Billed To
                    self.set_font("helvetica", "B", 9)
                    self.cell(40, 4, "Invoice No", border=0, new_x="RIGHT", new_y="TOP")
                    self.cell(40, 4, "Invoice Date", border=0, new_x="RIGHT", new_y="TOP")
                    self.cell(0, 4, "Billed To", border=0, new_x="LMARGIN", new_y="NEXT")
                    
                    self.set_font("helvetica", "", 9)
                    self.cell(40, 4, clean_text(self.inv_no), border=0, new_x="RIGHT", new_y="TOP")
                    self.cell(40, 4, self.inv_date.strftime('%d %b %Y'), border=0, new_x="RIGHT", new_y="TOP")
                    self.cell(0, 4, clean_text(self.billed_to_name) if self.billed_to_name else "Client Name", border=0, new_x="LMARGIN", new_y="NEXT")
                    
                    self.ln(5)

                    # --- Page Number & Disclaimer ---
                    self.set_font("helvetica", "B", 9)
                    self.cell(0, 6, f"Page {self.page_no()} of {{nb}}", align="L", new_x="LMARGIN", new_y="NEXT")
                    
                    self.set_font("helvetica", "", 8)
                    self.set_text_color(128, 128, 128)
                    self.cell(0, 4, "This is an electronically generated document, no signature is required.", align="L", new_x="LMARGIN", new_y="NEXT")
                    self.set_text_color(0, 0, 0)

            pdf = InvoicePDF(invoice_number, invoice_date, to_name)
            pdf.alias_nb_pages() # Required for {nb} to be replaced with total pages
            
            # VERY IMPORTANT: Set the auto page break high enough so the table 
            # stops drawing BEFORE it crashes into our custom 45mm tall footer.
            pdf.set_auto_page_break(auto=True, margin=50) 
            
            pdf.add_page()
            
            # Logo on the Top Right
            try:
                req = urllib.request.Request(logo_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as response:
                    img_data = response.read()
                    # Place logo on the top right. Page width is ~210mm.
                    pdf.image(io.BytesIO(img_data), x=155, y=10, w=40)
            except Exception:
                pass 
            
            # Top Header - Left: Invoice Details
            pdf.set_font("helvetica", "B", 24)
            pdf.set_y(15)
            pdf.cell(100, 10, "INVOICE", new_x="LMARGIN", new_y="NEXT", align="L")
            pdf.ln(5)
            
            pdf.set_font("helvetica", "B", 10)
            pdf.cell(35, 6, "Invoice Number:", new_x="RIGHT", new_y="TOP")
            pdf.set_font("helvetica", "", 10)
            pdf.cell(65, 6, clean_text(invoice_number), new_x="LMARGIN", new_y="NEXT")
            
            pdf.set_font("helvetica", "B", 10)
            pdf.cell(35, 6, "Invoice Date:", new_x="RIGHT", new_y="TOP")
            pdf.set_font("helvetica", "", 10)
            pdf.cell(65, 6, f"{invoice_date.strftime('%d %b %Y')}", new_x="LMARGIN", new_y="NEXT")
            
            pdf.set_font("helvetica", "B", 10)
            pdf.cell(35, 6, "Due Date:", new_x="RIGHT", new_y="TOP")
            pdf.set_font("helvetica", "", 10)
            pdf.cell(65, 6, f"{due_date.strftime('%d %b %Y')}", new_x="LMARGIN", new_y="NEXT")
            
            pdf.ln(15)
            
            # Billed By (Left Side)
            y_before_address = pdf.get_y()
            pdf.set_font("helvetica", "B", 12)
            pdf.cell(100, 6, "Billed By", new_x="LMARGIN", new_y="NEXT")
            
            pdf.set_font("helvetica", "B", 10)
            if billed_by.get('Company Name'):
                pdf.cell(100, 5, clean_text(billed_by.get('Company Name', '')), new_x="LMARGIN", new_y="NEXT")
            
            pdf.set_font("helvetica", "", 10)
            if billed_by.get('Address Line 1'):
                pdf.cell(100, 5, clean_text(billed_by.get('Address Line 1', '')), new_x="LMARGIN", new_y="NEXT")
            if billed_by.get('Address Line 2'):
                pdf.cell(100, 5, clean_text(billed_by.get('Address Line 2', '')), new_x="LMARGIN", new_y="NEXT")
            if billed_by.get('GSTIN'):
                pdf.cell(100, 5, clean_text(f"GSTIN: {billed_by.get('GSTIN', '')}"), new_x="LMARGIN", new_y="NEXT")
            if billed_by.get('PAN'):
                pdf.cell(100, 5, clean_text(f"PAN: {billed_by.get('PAN', '')}"), new_x="LMARGIN", new_y="NEXT")
            if billed_by.get('Phone'):
                pdf.cell(100, 5, clean_text(f"Phone: {billed_by.get('Phone', '')}"), new_x="LMARGIN", new_y="NEXT")
            
            # Billed To (Right Side)
            # Move up and set right margin for 2-column layout
            pdf.set_y(y_before_address)
            pdf.set_left_margin(115)
            
            pdf.set_font("helvetica", "B", 12)
            pdf.cell(0, 6, "Billed To", new_x="LMARGIN", new_y="NEXT")
            
            pdf.set_font("helvetica", "B", 10)
            if to_name:
                pdf.cell(0, 5, clean_text(to_name), new_x="LMARGIN", new_y="NEXT")
            
            pdf.set_font("helvetica", "", 10)
            for line in to_address.split('\n'):
                if line.strip():
                    pdf.cell(0, 5, clean_text(line.strip()), new_x="LMARGIN", new_y="NEXT")
            
            pdf.cell(0, 5, clean_text(f"State: {to_state}"), new_x="LMARGIN", new_y="NEXT")
            if to_gstin:
                pdf.cell(0, 5, clean_text(f"GSTIN: {to_gstin}"), new_x="LMARGIN", new_y="NEXT")
            if to_pan:
                pdf.cell(0, 5, clean_text(f"PAN: {to_pan}"), new_x="LMARGIN", new_y="NEXT")
            if to_phone and str(to_phone).strip() != "":
                pdf.cell(0, 5, clean_text(f"Phone: {to_phone}"), new_x="LMARGIN", new_y="NEXT")
            
            # Reset Margin for Table
            # Make Y coord lower than both columns
            pdf.set_left_margin(10)
            pdf.set_y(max(pdf.get_y(), y_before_address + 50) + 10)
            
            # Table Header Function
            def draw_table_header():
                pdf.set_font("helvetica", "B", 9)
                pdf.set_fill_color(240, 240, 240)
                
                # Widths: S.No=7, Item=44, HSN=15, MRP=11, Disc=8, GST=9, Rate=14, Qty=9, BaseAmt=20, CGST=18, SGST=18, IGST=36, Total=25/7
                # Total Width 190.
                pdf.cell(7, 8, "", border=1, new_x="RIGHT", new_y="TOP", align="C", fill=True)
                pdf.cell(44, 8, "Item", border=1, new_x="RIGHT", new_y="TOP", fill=True)
                pdf.cell(15, 8, "HSN", border=1, new_x="RIGHT", new_y="TOP", align="C", fill=True)
                pdf.cell(11, 8, "MRP", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
                pdf.cell(9, 8, "GST%", border=1, new_x="RIGHT", new_y="TOP", align="C", fill=True)
                pdf.cell(14, 8, "Rate", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
                pdf.cell(9, 8, "Qty", border=1, new_x="RIGHT", new_y="TOP", align="C", fill=True)
                pdf.cell(20, 8, "Amount", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
                
                if is_igst:
                    pdf.cell(36, 8, "IGST", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
                    pdf.cell(25, 8, "Total", border=1, new_x="LMARGIN", new_y="NEXT", align="R", fill=True)
                else:
                    pdf.cell(18, 8, "CGST", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
                    pdf.cell(18, 8, "SGST", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
                    pdf.cell(25, 8, "Total", border=1, new_x="LMARGIN", new_y="NEXT", align="R", fill=True)

            is_igst = from_state != to_state
            draw_table_header()
            
            # Table Rows
            pdf.set_font("helvetica", "", 9)
            for idx1, item1 in enumerate(invoice_items):
                text_w = 44 # Item column width
                text_lh = 4 # Less space between lines
                min_row_h = 8
                
                # Use multi_cell with dry_run to calculate lines instead of deprecated split_only
                lines = pdf.multi_cell(text_w, text_lh, str(item1['product']), border=0, align="L", dry_run=True, output="LINES")
                required_h = len(lines) * text_lh
                row_h = max(min_row_h, required_h)
                
                if pdf.will_page_break(row_h):
                    pdf.add_page()
                    draw_table_header()
                    pdf.set_font("helvetica", "", 9)

                # Draw cells
                curr_x1 = pdf.get_x()
                curr_y1 = pdf.get_y()
                
                # S.No
                pdf.cell(7, row_h, str(idx1 + 1), border=1, new_x="RIGHT", new_y="TOP", align="C")
                
                # Item Name (Multi-line) centered vertically
                y_offset = (row_h - required_h) / 2
                pdf.set_xy(curr_x1 + 7, curr_y1 + y_offset)
                pdf.multi_cell(text_w, text_lh, clean_text(item1['product']), border=0, align="L", new_x="RIGHT", new_y="TOP")
                pdf.rect(curr_x1 + 7, curr_y1, text_w, row_h)
                pdf.set_xy(curr_x1 + 7 + text_w, curr_y1)
                
                # Other columns
                pdf.cell(15, row_h, clean_text(item1.get('hsn', '')), border=1, new_x="RIGHT", new_y="TOP", align="C")
                pdf.cell(11, row_h, f"{int(item1.get('mrp', 0))}", border=1, new_x="RIGHT", new_y="TOP", align="R")
                pdf.cell(9, row_h, f"{item1['gst_percent']}%", border=1, new_x="RIGHT", new_y="TOP", align="C")
                pdf.cell(14, row_h, f"{item1['price']:,.2f}", border=1, new_x="RIGHT", new_y="TOP", align="R")
                pdf.cell(9, row_h, str(item1['qty']), border=1, new_x="RIGHT", new_y="TOP", align="C")
                pdf.cell(20, row_h, f"{item1['base_total']:,.2f}", border=1, new_x="RIGHT", new_y="TOP", align="R")
                
                if is_igst:
                    pdf.cell(36, row_h, f"{item1['igst']:,.2f}", border=1, new_x="RIGHT", new_y="TOP", align="R")
                    pdf.cell(25, row_h, f"{item1['total']:,.2f}", border=1, new_x="LMARGIN", new_y="NEXT", align="R")
                else:
                    pdf.cell(18, row_h, f"{item1['cgst']:,.2f}", border=1, new_x="RIGHT", new_y="TOP", align="R")
                    pdf.cell(18, row_h, f"{item1['sgst']:,.2f}", border=1, new_x="RIGHT", new_y="TOP", align="R")
                    pdf.cell(25, row_h, f"{item1['total']:,.2f}", border=1, new_x="LMARGIN", new_y="NEXT", align="R")
                    
            # Total Row inside the table
            if pdf.will_page_break(8):
                pdf.add_page()
                draw_table_header()

            pdf.set_font("helvetica", "B", 9)
            pdf.set_fill_color(240, 240, 240)
            
            total_qty_sum = df["qty"].sum()
            pdf.cell(7 + 44 + 15 + 11 + 9 + 14, 8, "Total", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
            pdf.cell(9, 8, str(total_qty_sum), border=1, new_x="RIGHT", new_y="TOP", align="C", fill=True)
            pdf.cell(20, 8, f"{subtotal:,.2f}", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
            
            if is_igst:
                pdf.cell(36, 8, f"{total_igst:,.2f}", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
                pdf.cell(25, 8, f"{grand_total:,.2f}", border=1, new_x="LMARGIN", new_y="NEXT", align="R", fill=True)
            else:
                pdf.cell(18, 8, f"{total_cgst:,.2f}", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
                pdf.cell(18, 8, f"{total_sgst:,.2f}", border=1, new_x="RIGHT", new_y="TOP", align="R", fill=True)
                pdf.cell(25, 8, f"{grand_total:,.2f}", border=1, new_x="LMARGIN", new_y="NEXT", align="R", fill=True)
                    
            pdf.ln(5)
            
            # Totals Footer
            pdf.set_left_margin(120)
            pdf.set_font("helvetica", "", 10)
            pdf.cell(30, 6, "Subtotal:", new_x="RIGHT", new_y="TOP", align="R")
            pdf.cell(40, 6, f"Rs. {subtotal:,.2f}", new_x="LMARGIN", new_y="NEXT", align="R")
            
            if not is_igst:
                pdf.cell(30, 6, "CGST:", new_x="RIGHT", new_y="TOP", align="R")
                pdf.cell(40, 6, f"Rs. {total_cgst:,.2f}", new_x="LMARGIN", new_y="NEXT", align="R")
                pdf.cell(30, 6, "SGST:", new_x="RIGHT", new_y="TOP", align="R")
                pdf.cell(40, 6, f"Rs. {total_sgst:,.2f}", new_x="LMARGIN", new_y="NEXT", align="R")
            else:
                pdf.cell(30, 6, "IGST:", new_x="RIGHT", new_y="TOP", align="R")
                pdf.cell(40, 6, f"Rs. {total_igst:,.2f}", new_x="LMARGIN", new_y="NEXT", align="R")
                
            pdf.set_font("helvetica", "B", 12)
            pdf.cell(30, 8, "Grand Total:", new_x="RIGHT", new_y="TOP", align="R")
            pdf.cell(40, 8, f"Rs. {grand_total:,.2f}", new_x="LMARGIN", new_y="NEXT", align="R")
            
            pdf.set_left_margin(10)
            return bytes(pdf.output())

        # Call the function
        pdf_bytes = generate_pdf()
    except Exception as e:
        st.error(f"❌ PDF Generation Error: {str(e)}")
        st.info("Check your Streamlit Cloud logs or Ensure 'fpdf2' is in requirements.txt.")

    action1, action2 = st.columns(2)
    
    with action1:
        # Instead of a button that triggers a download, we use a form to handle state
        # But Streamlit doesn't allow download_button inside a form execution natively easily
        # So we use a st.button that sets a flag in session state to show a success message
        if st.button("💾 Save & Download"):
            try:
                creds = get_gcp_creds()
                # 1. Upload to Google Drive First
                drive_link = ""
                try:
                    drive_service = build('drive', 'v3', credentials=creds)
                    file_metadata = {
                        'name': f"{invoice_number}.pdf",
                        'parents': ['1lDGSAc6cyNP-nZuUZFRPmj0SDfdpLpy6']
                    }
                    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype='application/pdf')
                    drive_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
                    drive_link = drive_file.get('webViewLink', '')
                except Exception as drive_e:
                    st.warning(f"Failed to upload to Drive: {str(drive_e)}")

                # 2. Save to Google Sheets
                client = gspread.authorize(creds)
                spreadsheet = client.open_by_key('1msnl_ZYZTvl1j45mjPI9FvzXDphJNLsOPfhyNxanK5I')
                invoices_sheet = spreadsheet.worksheet('Invoices')
                
                # S.No, Invoice No, Date, Due Date, Client Name, Subtotal, CGST, SGST, IGST, Grand Total, Drive Link
                s_no = len(invoices_sheet.get_all_values())  # Rows including header, so len is next S.no sequence
                
                row_data = [
                    s_no,
                    invoice_number,
                    invoice_date.strftime('%Y-%m-%d'),
                    due_date.strftime('%Y-%m-%d'),
                    to_name,
                    float(round(subtotal, 2)),
                    float(round(total_cgst, 2)),
                    float(round(total_sgst, 2)),
                    float(round(total_igst, 2)),
                    float(round(grand_total, 2)),
                    drive_link
                ]
                invoices_sheet.append_row(row_data)
                
                # Auto increment local tracker after successful save
                st.session_state.invoice_num_override = get_next_alpha_numeric(invoice_number)
                
                # 3. Trigger Auto-Download Hack
                import base64
                b64 = base64.b64encode(pdf_bytes).decode()
                href = f'<a id="auto-dl" href="data:application/pdf;base64,{b64}" download="{invoice_number}.pdf"></a><script>document.getElementById("auto-dl").click();</script>'
                st.components.v1.html(href, height=0)
                
                if drive_link:
                    st.success(f"Invoice successfully saved, downloaded, and uploaded to Drive! [View in Drive]({drive_link})")
                else:    
                    st.success("Invoice successfully saved to sheets and downloaded! (Drive upload bypassed)")
                    
            except Exception as e:
                st.error(f"Failed to save invoice: {str(e)}")
                

    with action2:
        if st.button("➕ Create New Invoice"):
            # Clear all item rows
            st.session_state.item_rows = 1
            
            if 'show_download_link' in st.session_state:
                del st.session_state['show_download_link']
            
            # Reset all basic input keys
            keys_to_delete = [
                "invoice_no_input", "invoice_date_input", "invoice_due_date_input", 
                "client_select_input", "to_name_input", "to_address_input", 
                "to_state_input", "to_gstin_input", "to_pan_input", "to_phone_input"
            ]
            
            # Reset product selection keys based on however many lines they had
            for i in range(100):
                keys_to_delete.extend([
                    f"prod_select_{i}", f"prod_name_{i}", f"hsn_{i}", f"mrp_{i}",
                    f"ind_discount_{i}", f"qty_{i}", f"price_{i}", f"gst_{i}"
                ])
                
            for key in keys_to_delete:
                if key in st.session_state:
                    del st.session_state[key]
            
            # Fetch the factual latest invoice number from sheet directly
            st.cache_data.clear() # Clear cache to ensure we get fresh data
            st.session_state.invoice_num_override = fetch_latest_invoice_number_from_sheet()
            st.rerun()
else:
    st.info("Please enter at least one product to see the invoice summary.")
