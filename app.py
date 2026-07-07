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

# --- BRAND NEW EXPLICIT FIREWALL FILTER ---
def is_genuine_quenching_message(text_content):
    """
    Instantly drops casual chat, system messages, and media updates before parsing.
    Returns True ONLY if the text matches real quenching data signatures.
    """
    txt_lower = text_content.lower()
    
    if "media omitted" in txt_lower or "file attached" in txt_lower or "sticker omitted" in txt_lower:
        return False
        
    has_heat = bool(re.search(r'heat', txt_lower))
    has_billet = bool(re.search(r'billet|bullet|bilet|billete', txt_lower))
    has_pqs_metrics = bool(re.search(r'pqs|fcv|flow|fliw|follow|speed|temp|carriage|pump|bar\s*temp', txt_lower))
    has_numbers = bool(re.search(r'\d', txt_lower))
    
    if has_heat and has_numbers:
        return True
    if has_billet and (has_pqs_metrics or has_numbers):
        return True
    if has_pqs_metrics and has_numbers:
        return True
        
    return False

def get_shift(hour):
    if 8 <= hour < 16: return 'A'
    elif 16 <= hour < 23: return 'B'
    else: return 'C'

def parse_whatsapp_data(text_content, sender_mapping, is_dayfirst_input, is_dayfirst_output, is_12hr):
    timestamp_pattern = r'\[?(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}(?:,?\s+|\s+)\d{1,2}[:\.]\d{2}(?:[:\.]\d{2})?(?:\s*[\u202f\u200e\s]*[APap][Mm])?)\]?'
    message_splits = re.split(timestamp_pattern, text_content)

    if len(message_splits) < 3:
        fallback_splits = re.split(r'(?:Heat No:|Heat #)[\s]*', text_content, flags=re.IGNORECASE)
        message_splits = [""]
        fake_time = "12/12/2024 12:00 PM"
        for f_block in fallback_splits[1:]:
            message_splits.append(fake_time)
            message_splits.append("Heat No: " + f_block)

    all_data = []
    
    keys_list = [
        "Sample No.", "Heat#", "Size", "Size Details", "Billet Qty", "Shift", "Shared By", "Time", "Date", 
        "PQS Carriage", "P1", "P2", "P3", "P4", "P5", "P6", "Pumps in Operation", "Mill Speed m/s", 
        "Flow Rate m3", "FCV%", "CE", "After WHF Temp.", "WHF Exit Temp At Stand 1 Entry", 
        "Bar Temp. Before PQS", "Bar Temp at Cooling Bed", "PQS Water Temperature"
    ]

    def get_num(keyword, text):
        m = re.search(rf'{keyword}[^\d\n]*(\d+(?:[\.\:]\d+)?)', text, re.IGNORECASE)
        return m.group(1).replace(':', '.') if m else ""

    for i in range(1, len(message_splits), 2):
        ts_str = message_splits[i].strip()
        record = message_splits[i+1].replace('<This message was edited>', '').replace('*', '').strip()
        
        if not is_genuine_quenching_message(record):
            continue

        try:
            msg_ts = parser.parse(ts_str, fuzzy=True, dayfirst=is_dayfirst_input)
            row = {k: "" for k in keys_list}
            
            if is_12hr:
                row["Time"] = msg_ts.strftime("%I:%M %p")
            else:
                row["Time"] = msg_ts.strftime("%H:%M")
                
            if is_dayfirst_output:
                row["Date"] = msg_ts.strftime("%d/%m/%Y")
            else:
                row["Date"] = msg_ts.strftime("%m/%d/%Y")
                
            row["Shift"] = get_shift(msg_ts.hour)
            row["_dt_obj"] = msg_ts.date()
            row["_raw_dt"] = msg_ts
            
            sender = "Unknown Number"
            s_match = re.search(r'^(?:\]|,|-)?\s*([^:\n🚨]+):', record)
            if s_match: 
                sender = s_match.group(1).strip()
                record_body = record[s_match.end():].strip()
            else:
                record_body = record.strip()
                
            sender = sender.replace('[', '').replace(']', '').strip()
            if sender in sender_mapping: sender = sender_mapping[sender]
            row["Shared By"] = sender

            text_lower = record_body.lower()

            b_match = re.search(r'(\d+)(?:st|nd|rd|th)?\s*(?:billet|bullet|bilet|billete)', text_lower)
            if not b_match:
                b_match = re.search(r'(?:billet|bullet|bilet|billete)[^\d\n]*(\d+)', text_lower)
            billets = int(b_match.group(1)) if b_match else 0
            
            row["Heat#"] = get_num(r'heat', record_body)
            row["Billet Qty"] = billets if billets > 0 else ""
            row["Size"] = get_num(r'size', record_body)
            
            sd_match = re.search(r'\(\s*([a-zA-Z\s]+)\s*\)', record_body[:150])
            if sd_match:
                val = sd_match.group(1).strip()
                if val.lower() not in ['bar', 'c', 'mm', 'omitted', 'attached']:
                    row["Size Details"] = val.title()
            
            row["Mill Speed m/s"] = get_num(r'sp[e]{1,2}d', record_body)
            row["Flow Rate m3"] = get_num(r'(?:fl[o]{1,2}w|f[o]{1,2}ll[o]{1,2}w|fl[i]{1,2}w|follow)', record_body)
            row["FCV%"] = get_num(r'fcv', record_body)
            
            ce_match = re.search(r'c\.?e\.?[\s:]*(semi\s*hot[\s\w]*|cold|\d+(?:\.\d*)?)', record_body, re.IGNORECASE)
            if ce_match:
                row["CE"] = ce_match.group(1).strip().title()
            
            carriage_match = re.search(r'carriage[\s:,\-\.#=]*([^\n]+)', record_body, re.IGNORECASE)
            if carriage_match:
                clean_carriage = re.sub(r'[\d\-\u2192\u2794\u27A1🔵🟡]', '', carriage_match.group(1))
                row["PQS Carriage"] = clean_carriage.strip()

            pump_match = re.search(r'pump[s]?[\s:,\-\.#=]*([^\n]+)', record_body, re.IGNORECASE)
            if pump_match:
                row["Pumps in Operation"] = pump_match.group(1).strip()

            row["After WHF Temp."] = get_num(r'(?:after|afr)\s*whf', record_body)
            # Updated to support both "Stand 1" and "Stand entry" without needing the '1'
            row["WHF Exit Temp At Stand 1 Entry"] = get_num(r'(?:stand|stnd|stad)(?:\s*1|\s*entry)?', record_body) 
            row["Bar Temp. Before PQS"] = get_num(r'before\s*pqs', record_body)
            row["Bar Temp at Cooling Bed"] = get_num(r'(?:cooling|coling|c\.?b\.?)', record_body)
            row["PQS Water Temperature"] = get_num(r'(?:water|watr)\s*temp', record_body)

            found_labeled = False
            for p_idx in range(1, 7):
                p_match = re.search(rf'(?:\*|\b){p_idx}\s*#\s*\*?\s*(?:->|\u2192|→|:)*\s*(\d+(?:\.\d+)?)', record_body, re.IGNORECASE)
                if p_match: 
                    row[f"P{p_idx}"] = p_match.group(1)
                    found_labeled = True

            if not found_labeled:
                naked_nums = []
                for line in record_body.split('\n'):
                    cln = line.strip()
                    if re.match(r'^[\s]*(\d+(?:\.\d+)?)[\s]*$', cln):
                        naked_nums.append(cln)
                if 0 < len(naked_nums) <= 6:
                    for p_idx, val in enumerate(naked_nums):
                        row[f"P{p_idx+1}"] = val

            all_data.append(row)
        except Exception:
            continue

    return pd.DataFrame(all_data)

# --- UI DESIGN ---
st.title("🔥 Quenching Parameters Extractor Pro")
st.markdown("Convert raw unstructured parameters into analytical engineering records instantly.")

with st.sidebar:
    st.header("⚙️ System Configuration")
    
    st.subheader("📅 Date & Time Formats")
    input_date_format = st.radio("Parser Reading Format (Log Input Source):", options=["Month First (US: 5/24/2026)", "Day First (UK: 24/05/2026)"], index=0)
    user_is_dayfirst_input = True if "Day First" in input_date_format else False
    
    output_date_format = st.radio("Excel Output Date Format:", options=["MM/DD/YYYY", "DD/MM/YYYY"], index=1)
    user_is_dayfirst_output = True if "DD/MM" in output_date_format else False
    
    output_time_format = st.radio("Excel Output Time Format:", options=["12-Hour (08:15 PM)", "24-Hour (20:15)"], index=0)
    user_is_12hr = True if "12-Hour" in output_time_format else False
    
    st.divider()
    
    st.subheader("👥 Employee Mapping")
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
            with open(mapping_file, "r") as f: current_list = json.load(f)
        except: current_list = default_list
    else: current_list = default_list
        
    edited_mapping = st.data_editor(pd.DataFrame(current_list), num_rows="dynamic", use_container_width=True)
    if st.button("💾 Save Employee Database"):
        with open(mapping_file, "w") as f: json.dump(edited_mapping.to_dict(orient="records"), f)
        st.success("Mapping Saved!")
        st.rerun()

    sender_dict = dict(zip(edited_mapping["Raw Number/Name"], edited_mapping["Employee Name"]))

st.header("📤 Upload & Streamline Data")
uploaded_file = st.file_uploader("Upload WhatsApp Export Log (.txt)", type=["txt"])

if uploaded_file is not None:
    content = uploaded_file.read().decode("utf-8", errors="ignore")
    
    with st.spinner('Applying operational validation firewall filters...'):
        result_df = parse_whatsapp_data(content, sender_dict, user_is_dayfirst_input, user_is_dayfirst_output, user_is_12hr)
        
    if result_df.empty:
        st.error("Firewall blocked all rows: No authentic quenching logs discovered in the source document.")
    else:
        st.divider()
        st.subheader("⏳ Workspace Filter Constraints")
        
        min_date, max_date = result_df['_dt_obj'].min(), result_df['_dt_obj'].max()
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if min_date == max_date:
                selected_date = st.date_input("Logs Date Found", value=min_date)
                filtered_df = result_df[result_df['_dt_obj'] == selected_date]
            else:
                selected_range = st.date_input("Select Active Window", value=(min_date, max_date), min_value=min_date, max_value=max_date)
                if isinstance(selected_range, tuple) and len(selected_range) == 2:
                    filtered_df = result_df[(result_df['_dt_obj'] >= selected_range[0]) & (result_df['_dt_obj'] <= selected_range[1])]
                else:
                    filtered_df = result_df
                    
        with col2:
            available_shifts = sorted(result_df['Shift'].unique().tolist())
            selected_shifts = st.multiselect("Filter Shifts", available_shifts, default=available_shifts)
            filtered_df = filtered_df[filtered_df['Shift'].isin(selected_shifts)]
            
        with col3:
            sort_order = st.selectbox("Display Ordering Matrix:", options=["Oldest First (Chronological)", "Newest First (Reverse Chronological)"])

        df_chrono = filtered_df.sort_values('_raw_dt', ascending=True).reset_index(drop=True)
        
        current_sample_no = 1
        sample_nos = []
        for idx, row in df_chrono.iterrows():
            sample_nos.append(current_sample_no)
            qty = row['Billet Qty']
            try:
                if qty != "" and int(qty) > 0: current_sample_no += int(qty)
            except ValueError: pass
                
        df_chrono['Sample No.'] = sample_nos
        final_df = df_chrono if "Oldest First" in sort_order else df_chrono.iloc[::-1].reset_index(drop=True)
        
        display_df = final_df.drop(columns=['_dt_obj', '_raw_dt'], errors='ignore')
        st.success(f"Successfully purified data! Showing {len(display_df)} verified entries (All raw chatter & empty lines eliminated).")
        st.dataframe(display_df, use_container_width=True)
        
        st.divider()
        buffer = io.BytesIO()
        display_df.to_excel(buffer, index=False)
        st.download_button(
            label="📥 Download Purified Excel Spreadsheet (.xlsx)",
            data=buffer.getvalue(),
            file_name="Purified_Quenching_Logs.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
