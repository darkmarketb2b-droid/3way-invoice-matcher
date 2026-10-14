
import csv
import html
import io
import json
import os
import random
import time
from datetime import datetime

import pandas as pd
import pdfplumber
import pytesseract
import streamlit as st
from openai import OpenAI

from pdf2image import convert_from_bytes
from PIL import Image

# =============================================================================
# CONFIG
# =============================================================================
st.set_page_config(page_title="3-Way Matcher | Interactive Demo", layout="wide", page_icon="📄")

MAX_DEMO_RUNS = 5            # ek session mein max live AI runs (aapki key ka kharcha bachane ke liye)
LEADS_FILE = "leads.csv"     # lead generation form yahan save hoga


def get_demo_key():
    """Aapki pre-configured key. .streamlit/secrets.toml mein DEMO_DEEPSEEK_KEY ya env var rakhein."""
    try:
        key = st.secrets.get("DEMO_DEEPSEEK_KEY", "")
    except Exception:
        key = ""
    return key or os.environ.get("DEMO_DEEPSEEK_KEY", "")


DEMO_KEY = get_demo_key()

# =============================================================================
# SAMPLE DATA (jaan-boojhkar 1 discrepancy: bolt qty + rate mismatch)
# =============================================================================
SAMPLE_PO = """PURCHASE ORDER  PO-10294
Vendor: ABC Suppliers Ltd
1. Steel Rods        Qty 100   Rate 450.00   Amount 45,000.00
2. SS Bolt 10mm      Qty 500   Rate 12.00    Amount 6,000.00
3. Hex Nut M10       Qty 1000  Rate 3.00     Amount 3,000.00
Total: 54,000.00"""

SAMPLE_GRN = """GOODS RECEIPT NOTE  GRN-5581  (Ref PO-10294)
1. Steel Rods                          Received 100
2. Stainless Steel Fastener 10 Millimeter  Received 480
3. Hex Nut M10                         Received 1000"""

SAMPLE_BILL = """VENDOR INVOICE  INV-7781   PO Ref: PO-10294
ABC Suppliers Ltd
1. Steel Rods        Qty 100   Rate 450.00   Amount 45,000.00
2. SS Bolt 10mm      Qty 500   Rate 12.50    Amount 6,250.00
3. Hex Nut M10       Qty 1000  Rate 3.00     Amount 3,000.00
Total: 54,250.00"""

# Offline fallback: demo key na ho tab bhi sample test 5 second mein chalega
SIMULATED_RESULT = {
    "match_status": "DISCREPANCY_FOUND",
    "po_number": "PO-10294",
    "vendor_name": "ABC Suppliers Ltd",
    "bill_amount": 54250.00,
    "line_items": [
        {"item_name": "Steel Rods", "po_qty": 100, "grn_qty": 100, "bill_qty": 100,
         "po_price": 450.0, "bill_price": 450.0, "bill_total": 45000.0,
         "status": "MATCH", "note": "PO, GRN aur Bill teeno match."},
        {"item_name": "SS Bolt 10mm", "po_qty": 500, "grn_qty": 480, "bill_qty": 500,
         "po_price": 12.0, "bill_price": 12.5, "bill_total": 6250.0,
         "status": "MISMATCH",
         "note": "Bill qty 500 hai par GRN mein sirf 480 mile; rate PO ₹12.00 vs Bill ₹12.50."},
        {"item_name": "Hex Nut M10", "po_qty": 1000, "grn_qty": 1000, "bill_qty": 1000,
         "po_price": 3.0, "bill_price": 3.0, "bill_total": 3000.0,
         "status": "MATCH", "note": "PO, GRN aur Bill teeno match."},
    ],
    "discrepancies": [
        {"item": "SS Bolt 10mm", "issue": "Over-billing: 20 units ka bill bina receipt ke (GRN 480 vs Bill 500)."},
        {"item": "SS Bolt 10mm", "issue": "Rate mismatch: PO ₹12.00 vs Bill ₹12.50 (+4.17%)."},
    ],
    "summary": "3 mein se 2 line items match hue. SS Bolt 10mm par qty aur rate dono mein antar hai. "
               "Estimated excess billing ≈ ₹490 (₹250 rate difference + ₹240 unreceived qty). "
               "Payment hold karke vendor se credit note maangna recommended hai.",
}

# =============================================================================
# HELPERS
# =============================================================================
def to_float(v, default=0.0):
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return default


def esc(v):
    return html.escape(str(v if v is not None else ""))
