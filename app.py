import streamlit as st
import pandas as pd
import re
from datetime import datetime
from dateutil import parser
import io
import os
import json

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Quenching Parameters Processor", page_icon="🔥", layout="wide")

# --- CORE LOGIC ---
def get_shift(hour):
    if 8 <= hour < 16: return 'A'
    elif 16 <= hour < 23: return 'B'
    else: return 'C'

def parse_whatsapp_data(text_content, sender_mapping, is_dayfirst_input, is_dayfirst_output, is_12hr):
    # Matches timestamp patterns reliably across different device exports
    timestamp_pattern = r'(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}(?:,?\s+|\s+)\d{1,2}[:\.]\d{2}(?:\s*[\u202f\u200e\s]*[APap][Mm])?)'
    message_splits = re.split(timestamp_pattern, text_content)

    all_data = []
    
    keys_list = [
        "Sample No.", "Heat#", "Size", "Size Details", "Billet Qty", "Shift", "Shared By", "Time", "Date", 
        "PQS Carriage", "P1", "P2", "P3", "P4", "P5", "P6", "Pumps in Operation", "Mill Speed m/s", 
        "Flow Rate m3", "FCV%", "After WHF Temp.", "WHF Exit Temp At Stand 1 Entry", 
        "Bar Temp. Before PQS", "Bar Temp at Cooling Bed", "PQS Water Temperature"
    ]

    def get_num(keyword, text):
        m = re.search(rf'{keyword}[^\d\n]*(\d+(?:\.\d+)?)', text, re.IGNORECASE)
        return m.group(1) if m else ""

    for i in range(1, len(message_splits), 2):
        ts_str = message_splits[i].strip()
        
        record = message_splits[i+1].replace('<This message was edited>', '').replace('*', '').strip()
        text_lower = record.lower()
        
        try:
            # Parse full datetime object
            msg_ts = parser.parse(ts_str, fuzzy=True, dayfirst=is_dayfirst_input)

            row = {k: "" for k in keys_list}
            
            # --- Apply Custom Formatting ---
            if is_12hr:
                row["Time"] = msg_ts.strftime("%I:%M %p")
            else:
                row["Time"] = msg_ts.strftime("%H:%M")
                
            if is_dayfirst_output:
                row["Date"] = msg_ts.strftime("%d/%m/%Y")
            else:
                row["Date"] = msg_ts.strftime("%m/%d/%Y")
                
            row["Shift"] = get_shift(msg_ts.hour)
            
            # Hidden fields for accurate sorting and filtering
            row["_dt_obj"] = msg_ts.date()
            row["_raw_dt"] = msg_ts
            
            # --- Sender Extraction ---
            sender = "Unknown Number"
            s_match = re.search(r'(?:\]|,|-)\s*([^:\n🚨]+):', record)
            if s_match: sender = s_match.group(1).strip()
            sender = sender.replace('[', '').replace(']', '').strip()
            if sender in sender_mapping: sender = sender_mapping[sender]
            row["Shared By"] = sender

            # --- Resilient Quantities Logic ---
            b_match = re.search(r'(\d+)\s*(?:billet|bullet|bilet|billete)', text_lower)
            if not b_match:
                b_match = re.search(r'(?:billet|bullet|bilet|billete)[^\d\n]*(\d+)', text_lower)
            billets = int(b_match.group(1)) if b_match else 0
            
            row["Heat#"] = get_num(r'heat', record)
            row["Billet Qty"] = billets if billets > 0 else ""

            # --- Sizes & Labels Extraction ---
            row["Size"] = get_num(r'size', record)
            
            sd_match = re.search(r'\(\s*([a-zA-Z\s]+)\s*\)', record[:150])
            if sd_match:
                val = sd_match.group(1).strip()
                if val.lower() not in ['bar', 'c', 'mm']:
                    row["Size Details"] = val.title()
            
            # --- PQS Metrics Extraction ---
            row["Mill Speed m/s"] = get_num(r'sp[e]{1,2}d', record)
            row["Flow Rate m3"] = get_num(r'(?:fl[o]{1,2}w|f[o]{1,2}ll[o]{1,2}w|fl[i]{1,2}w|follow)', record)
            row["FCV%"] = get_num(r'fcv', record)
            
            carriage_match = re.search(r'carriage[\s:,\-\.#=]*([^\n]+)', record, re.IGNORECASE)
            if carriage_match:
                clean_carriage = re.sub(r'[\d\-\u2192\u2794\u27A1🔵🟡]', '', carriage_match.group(1))
                row["PQS Carriage"] = clean_carriage.strip()

            pump_match = re.search(r'pump[s]?[\s:,\-\.#=]*([^\n]+)', record, re.IGNORECASE)
            if pump_match:
                row["Pumps in Operation"] = pump_match.group(1).strip()

            # --- Resilient Temperatures Extraction ---
            row["After WHF Temp."] = get_num(r'(?:after|afr)\s*whf', record)
            row["WHF Exit Temp At Stand 1 Entry"] = get_num(r'(?:stand|stnd|stad)\s*1', record)
            row["Bar Temp. Before PQS"] = get_num(r'before\s*pqs', record)
            row["Bar Temp at Cooling Bed"] = get_num(r'(?:cooling|coling|c\.?b\.?)', record)
            row["PQS Water Temperature"] = get_num(r'(?:water|watr)\s*temp', record)

            # --- Dynamic Pressure Logic ---
            found_labeled = False
            for p_idx in range(1, 7):
                p_match = re.search(rf'(?:\*|\b){p_idx}\s*#\s*\*?\s*(?:->|\u2192|→|:)*\s*(\d+(?:\.\d+)?)', record, re.IGNORECASE)
                if p_match: 
                    row[f"P{p_idx}"] = p_match.group(1)
                    found_labeled = True

            if not found_labeled:
                naked_nums = []
                for line in record.split('\n'):
                    cln = line.strip()
                    if re.match(r'^[\s]*(\d+(?:\.\d+)?)[\s]*$', cln):
                        naked_nums.append(cln.strip())
                
                if 0 < len(naked_nums) <= 6:
                    for p_idx, val in enumerate(naked_nums):
                        row[f"P{p_idx+1}"] = val

            all_data.append(row)

        except Exception:
            continue

    df = pd.DataFrame(all_data)
    return df

# --- UI DESIGN ---
st.title("🔥 Quenching Parameters Extractor")
st.markdown("Upload your WhatsApp chat export to instantly convert raw texts into structured datasets.")

with st.sidebar:
    st.header("⚙️ System Configuration")
    
    st.subheader("📅 Date & Time Hardware Formats")
    st.markdown("**1. How does the uploaded phone export dates?**")
    input_date_format = st.radio(
        "Parser Reading Format:",
        options=["Month First (US: 5/24/2026)", "Day First (UK: 24/05/2026)"],
        index=0,
        label_visibility="collapsed"
    )
    user_is_dayfirst_input = True if "Day First" in input_date_format else False
    
    st.markdown("**2. How do you want dates in Excel?**")
    output_date_format = st.radio(
        "Excel Date Format:",
        options=["MM/DD/YYYY", "DD/MM/YYYY"],
        index=1,
        label_visibility="collapsed"
    )
    user_is_dayfirst_output = True if "DD/MM" in output_date_format else False
    
    st.markdown("**3. How do you want time formatted?**")
    output_time_format = st.radio(
        "Excel Time Format:",
        options=["12-Hour (08:15 PM)", "24-Hour (20:15)"],
        index=0,
        label_visibility="collapsed"
    )
    user_is_12hr = True if "12-Hour" in output_time_format else False
    
    st.divider()
    
    st.subheader("👥 Employee Name Mapping")
    mapping_file = "saved_senders.json"
    
    default_list = [
        {"Raw Number/Name": "+92 346 2727806", "Employee Name": "Shahzad"},
        {"Raw Number/Name": "+92 315 8139861", "Employee Name": "Umair"},
        {"Raw Number/Name": "+92 307 1696112", "Employee Name": "Haque Nawaz"},
        {"Raw Number/Name": "+92 316 8632889", "Employee Name": "Danish"},
        {"Raw Number/Name": "+92 345 1684108", "Employee Name": "Mehboob"},
        {"Raw Number/Name": "+92 310 0082359", "Employee Name": "Wajeeh"}
    ]
    
    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, "r") as f:
                current_list = json.load(f)
        except:
            current_list = default_list
    else:
        current_list = default_list
        
    edited_mapping = st.data_editor(pd.DataFrame(current_list), num_rows="dynamic", use_container_width=True)
    
    if st.button("💾 Save Mapping Permanently"):
        new_data = edited_mapping.to_dict(orient="records")
        with open(mapping_file, "w") as f:
            json.dump(new_data, f)
        st.success("Saved!")
        st.rerun()

    sender_dict = dict(zip(edited_mapping["Raw Number/Name"], edited_mapping["Employee Name"]))

st.header("📤 Upload & Process")
uploaded_file = st.file_uploader("Upload WhatsApp Text File (.txt)", type=["txt"])

if uploaded_file is not None:
    @st.cache_data
    def load_data(file_content, mapping, dayfirst_in, dayfirst_out, is_12h):
        return parse_whatsapp_data(file_content, mapping, dayfirst_in, dayfirst_out, is_12h)

    content = uploaded_file.read().decode("utf-8", errors="ignore")
    
    with st.spinner('Parsing logs...'):
        result_df = load_data(content, sender_dict, user_is_dayfirst_input, user_is_dayfirst_output, user_is_12hr)
        
    if result_df.empty:
        st.warning("No records found in the log file.")
    else:
        st.divider()
        st.subheader("⏳ Filter & Arrangement Controls")
        
        # FIXED: Control parameters brought completely outside the sidebar panel
        min_date = result_df['_dt_obj'].min()
        max_date = result_df['_dt_obj'].max()
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if min_date == max_date:
                selected_date = st.date_input("Logs Date Found", value=min_date)
                filtered_df = result_df[result_df['_dt_obj'] == selected_date]
            else:
                selected_range = st.date_input(
                    "Select Date Window",
                    value=(min_date, max_date),
                    min_value=min_date,
                    max_value=max_date
                )
                if isinstance(selected_range, tuple) and len(selected_range) == 2:
                    s_date, e_date = selected_range
                    filtered_df = result_df[(result_df['_dt_obj'] >= s_date) & (result_df['_dt_obj'] <= e_date)]
                else:
                    filtered_df = result_df
                    
        with col2:
            available_shifts = sorted(result_df['Shift'].unique().tolist())
            selected_shifts = st.multiselect("Filter by Shift(s)", available_shifts, default=available_shifts)
            filtered_df = filtered_df[filtered_df['Shift'].isin(selected_shifts)]
            
        with col3:
            sort_order = st.selectbox(
                "Table Arrangement Order:", 
                options=["Oldest First (Chronological)", "Newest First (Reverse Chronological)"], 
                index=0
            )

        # FIXED: Core sequential processing engine sorted strictly by actual datetime objects
        df_chrono = filtered_df.sort_values('_raw_dt', ascending=True).reset_index(drop=True)
        
        # FIXED: Sample tracking recalculates starting from 1 sequentially relative only to filtered criteria
        current_sample_no = 1
        sample_nos = []
        for idx, row in df_chrono.iterrows():
            sample_nos.append(current_sample_no)
            qty = row['Billet Qty']
            try:
                if qty != "" and int(qty) > 0:
                    current_sample_no += int(qty)
            except ValueError:
                pass
                
        df_chrono['Sample No.'] = sample_nos
        
        # Apply the chosen visual data matrix layout mapping
        if "Oldest First" in sort_order:
            final_df = df_chrono
        else:
            final_df = df_chrono.iloc[::-1].reset_index(drop=True)
            
        # Strip out the hidden datetime tracking keys before rendering metrics layout
        display_df = final_df.copy()
        if '_dt_obj' in display_df.columns:
            display_df = display_df.drop(columns=['_dt_obj'])
        if '_raw_dt' in display_df.columns:
            display_df = display_df.drop(columns=['_raw_dt'])
            
        st.success(f"Showing {len(display_df)} records matching filters!")
        st.dataframe(display_df, use_container_width=True)
        
        st.divider()
        st.subheader("💾 Export Structured Data")
        
        buffer = io.BytesIO()
        display_df.to_excel(buffer, index=False)
        st.download_button(
            label="📥 Download as Excel (.xlsx)",
            data=buffer.getvalue(),
            file_name="Quenching_Parameters_Export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
