"""
📰 Newspaper Archive System
Clean, working version with proper text visibility
Run with: streamlit run streamlit_app.py
"""

import streamlit as st
import sqlite3
import requests
import pandas as pd
from datetime import datetime
import time
import json
from pathlib import Path
from typing import Dict, List, Tuple

# ==================== PAGE CONFIG ====================
st.set_page_config(
    page_title="Newspaper Archive",
    page_icon="📰",
    layout="wide"
)

# ==================== DATABASE SETUP ====================
DATABASE_NAME = 'newspaper_archive.db'
CONFIG_FILE = Path("api_keys.json")

def load_api_keys() -> Dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {'guardian': '', 'trove': ''}

def save_api_keys(keys: Dict) -> None:
    with open(CONFIG_FILE, 'w') as f:
        json.dump(keys, f)

def init_database():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT UNIQUE,
            title TEXT,
            newspaper_name TEXT,
            publication_date TEXT,
            content TEXT,
            page_url TEXT,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
            title, content, newspaper_name, source, publication_date,
            content=articles,
            content_rowid=id
        )
    ''')
    
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== DATA SOURCES ====================
class ChroniclingAmericaSource:
    SOURCE_NAME = "chronicling_america"
    BASE_URL = "https://chroniclingamerica.loc.gov"
    
    def import_date(self, year, month, day):
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        url = f"{self.BASE_URL}/search/pages/results/?date1={date_str}&date2={date_str}&format=json&rows=100"
        
        try:
            response = requests.get(url, timeout=10)
            data = response.json()
            items = data.get('items', [])
            if not items:
                return 0
            
            conn = get_db()
            cursor = conn.cursor()
            stored = 0
            
            for item in items:
                external_id = item.get('id')
                if not external_id:
                    continue
                
                cursor.execute('SELECT id FROM articles WHERE source = ? AND external_id = ?', 
                             (self.SOURCE_NAME, external_id))
                if cursor.fetchone():
                    continue
                
                title = item.get('title', 'Untitled')
                newspaper = item.get('title', 'Unknown')
                content = item.get('ocr_eng', '')
                page_url = f"{self.BASE_URL}{item.get('url', '')}"
                
                if content:
                    cursor.execute('''
                        INSERT INTO articles 
                        (source, external_id, title, newspaper_name, publication_date, content, page_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (self.SOURCE_NAME, external_id, title, newspaper, date_str, content[:5000], page_url))
                    stored += 1
            
            conn.commit()
            conn.close()
            return stored
        except:
            return 0

class GuardianSource:
    SOURCE_NAME = "guardian"
    BASE_URL = "https://content.guardianapis.com/search"
    
    def __init__(self, api_key=None):
        self.api_key = api_key
    
    def is_configured(self):
        return bool(self.api_key and self.api_key != '')
    
    def import_search(self, query, limit=20):
        if not self.is_configured():
            return 0, "API key required"
        
        params = {
            'api-key': self.api_key,
            'q': query,
            'format': 'json',
            'page-size': min(limit, 50),
            'show-fields': 'bodyText'
        }
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            if response.status_code != 200:
                return 0, "API error"
            
            data = response.json()
            articles = data.get('response', {}).get('results', [])
            
            conn = get_db()
            cursor = conn.cursor()
            stored = 0
            
            for article in articles:
                external_id = article.get('id')
                if not external_id:
                    continue
                
                cursor.execute('SELECT id FROM articles WHERE source = ? AND external_id = ?', 
                             (self.SOURCE_NAME, external_id))
                if cursor.fetchone():
                    continue
                
                title = article.get('webTitle', 'Untitled')
                date = article.get('webPublicationDate', '')[:10]
                content = article.get('fields', {}).get('bodyText', '')
                page_url = article.get('webUrl', '')
                
                if content:
                    cursor.execute('''
                        INSERT INTO articles 
                        (source, external_id, title, newspaper_name, publication_date, content, page_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (self.SOURCE_NAME, external_id, title, 'The Guardian', date, content[:5000], page_url))
                    stored += 1
            
            conn.commit()
            conn.close()
            return stored, "Success"
        except Exception as e:
            return 0, str(e)

# ==================== SEARCH FUNCTIONS ====================
def search_articles(query, limit=50):
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT a.id, a.source, a.title, a.newspaper_name, 
                   a.publication_date, a.content, a.page_url
            FROM articles a
            JOIN articles_fts f ON a.id = f.rowid
            WHERE articles_fts MATCH ?
            ORDER BY a.publication_date DESC
            LIMIT ?
        ''', (query, limit))
        
        results = [dict(row) for row in cursor.fetchall()]
        for r in results:
            if r['content'] and len(r['content']) > 300:
                r['content'] = r['content'][:300] + '...'
        return results
    except:
        return []
    finally:
        conn.close()

def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as total FROM articles')
    total = cursor.fetchone()['total']
    cursor.execute('SELECT COUNT(DISTINCT newspaper_name) as newspapers FROM articles')
    newspapers = cursor.fetchone()['newspapers']
    conn.close()
    return {'total': total, 'newspapers': newspapers}

# ==================== MAIN APP ====================
init_database()
api_keys = load_api_keys()
stats = get_stats()

# ==================== CLEAN, WORKING UI ====================

# Title
st.title("📰 Newspaper Archive")
st.markdown("Access historical and contemporary newspapers from trusted sources")

# Stats row
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Articles", f"{stats['total']:,}")
with col2:
    st.metric("Newspapers", f"{stats['newspapers']:,}")
with col3:
    st.metric("Data Sources", "3")

st.divider()

# ==================== IMPORT SECTION ====================
st.subheader("📥 Import Content")

tab1, tab2, tab3 = st.tabs(["🇺🇸 US Historical (Free)", "🇬🇧 The Guardian", "🇦🇺 Australian (Trove)"])

# Tab 1 - Chronicling America (Free)
with tab1:
    st.markdown("**Chronicling America - Library of Congress**")
    st.caption("Free access to US historical newspapers (1777-1963)")
    
    events = {
        "Apollo 11 Moon Landing (1969)": (1969, 7, 20),
        "Pearl Harbor Attack (1941)": (1941, 12, 7),
        "End of WWII (1945)": (1945, 9, 2),
        "Titanic Sinking (1912)": (1912, 4, 15),
        "JFK Assassination (1963)": (1963, 11, 22),
    }
    
    selected = st.multiselect("Select events to import", list(events.keys()), default=list(events.keys())[:2])
    
    if st.button("Import Selected Events", type="primary"):
        source = ChroniclingAmericaSource()
        progress = st.progress(0)
        status = st.empty()
        total = 0
        
        for i, event in enumerate(selected):
            year, month, day = events[event]
            status.text(f"Importing: {event}...")
            count = source.import_date(year, month, day)
            total += count
            progress.progress((i + 1) / len(selected))
            time.sleep(0.5)
        
        status.text(f"✅ Imported {total} articles")
        st.success(f"Successfully added {total} articles")
        time.sleep(1.5)
        st.rerun()

# Tab 2 - The Guardian
with tab2:
    st.markdown("**The Guardian**")
    st.caption("Modern news articles (1999-present)")
    
    if api_keys['guardian']:
        st.success("✅ API key configured")
        
        topics = st.multiselect(
            "Topics",
            ["Technology", "Science", "Climate", "Politics", "Arts"],
            default=["Technology", "Science"]
        )
        
        limit = st.slider("Articles per topic", 5, 30, 15)
        
        if st.button("Import from Guardian", type="primary"):
            source = GuardianSource(api_keys['guardian'])
            progress = st.progress(0)
            status = st.empty()
            total = 0
            
            for i, topic in enumerate(topics):
                status.text(f"Importing: {topic}...")
                count, msg = source.import_search(topic.lower(), limit)
                total += count
                progress.progress((i + 1) / len(topics))
                time.sleep(1)
            
            status.text(f"✅ Imported {total} articles")
            st.success(f"Successfully added {total} articles")
            time.sleep(1.5)
            st.rerun()
    else:
        st.warning("⚠️ API key required")
        st.info("Add your Guardian API key in the sidebar")
        
        guardian_input = st.text_input("Guardian API Key", type="password", key="guardian_input")
        if st.button("Save API Key"):
            api_keys['guardian'] = guardian_input
            save_api_keys(api_keys)
            st.success("API key saved!")
            st.rerun()
        
        st.caption("Get a free key at: https://open-platform.theguardian.com/access/")

# Tab 3 - Trove
with tab3:
    st.markdown("**Trove - National Library of Australia**")
    st.caption("Australian historical newspapers (1803-1954)")
    
    if api_keys['trove']:
        st.success("✅ API key configured")
        st.info("Trove API integration coming soon. Check back for updates!")
    else:
        st.warning("⚠️ API key required")
        st.info("Add your Trove API key in the sidebar")
        
        trove_input = st.text_input("Trove API Key", type="password", key="trove_input")
        if st.button("Save Trove Key"):
            api_keys['trove'] = trove_input
            save_api_keys(api_keys)
            st.success("API key saved!")
            st.rerun()
        
        st.caption("Get a free key at: https://trove.nla.gov.au/api")

st.divider()

# ==================== SEARCH SECTION ====================
st.subheader("🔍 Search Archive")

# Search input
search_term = st.text_input("", placeholder="Enter keywords, names, or topics...", label_visibility="collapsed")

col_search1, col_search2 = st.columns([1, 4])
with col_search1:
    search_clicked = st.button("Search", type="primary", use_container_width=True)
with col_search2:
    if st.button("Show Recent", use_container_width=True):
        search_term = "the"
        search_clicked = True

# Display results
if search_clicked and search_term:
    with st.spinner(f"Searching for '{search_term}'..."):
        results = search_articles(search_term)
    
    if results:
        st.success(f"Found {len(results)} articles")
        
        for article in results:
            with st.container():
                st.markdown(f"**📄 [{article['title']}]({article['page_url']})**")
                st.caption(f"📰 {article['newspaper_name']} | 📅 {article['publication_date'] or 'Date unknown'} | Source: {article['source'].replace('_', ' ').title()}")
                if article['content']:
                    st.text(article['content'][:200] + "...")
                st.markdown(f"[Read full article →]({article['page_url']})")
                st.divider()
    else:
        st.info("No results found. Try different keywords or import more articles.")

elif stats['total'] == 0:
    st.info("""
    ### 👋 Welcome to Newspaper Archive!
    
    **Get started:**
    1. Go to the **"Import Content"** section above
    2. Select events or topics to import
    3. Click **"Import"** to add articles
    4. Search your archive!
    
    *Chronicling America is free and requires no API key*
    """)
else:
    # Show recent articles
    st.markdown("#### Recently Added")
    recent = search_articles("the", limit=5)
    if recent:
        for article in recent:
            st.markdown(f"**📄 {article['title'][:80]}**")
            st.caption(f"{article['newspaper_name']} - {article['publication_date'] or 'Date unknown'}")
            st.markdown(f"[Read more →]({article['page_url']})")
            st.markdown("---")

# ==================== SIDEBAR ====================
with st.sidebar:
    st.markdown("### 📊 Statistics")
    st.metric("Total Articles", f"{stats['total']:,}")
    st.metric("Newspapers", f"{stats['newspapers']:,}")
    
    st.divider()
    
    st.markdown("### 🔧 Configuration")
    
    with st.expander("API Keys"):
        st.markdown("**The Guardian API**")
        guardian_val = st.text_input("API Key", value=api_keys['guardian'], type="password", key="sidebar_guardian")
        if st.button("Update Guardian Key"):
            api_keys['guardian'] = guardian_val
            save_api_keys(api_keys)
            st.success("Saved!")
            st.rerun()
        
        st.markdown("**Trove API**")
        trove_val = st.text_input("API Key", value=api_keys['trove'], type="password", key="sidebar_trove")
        if st.button("Update Trove Key"):
            api_keys['trove'] = trove_val
            save_api_keys(api_keys)
            st.success("Saved!")
            st.rerun()
    
    st.divider()
    
    st.markdown("### ℹ️ About")
    st.caption("""
    **Sources:**
    - Chronicling America (LOC) - Free
    - The Guardian - API key required
    - Trove (NLA) - API key required
    
    All articles link to original sources.
    """)
    
    if stats['total'] > 0:
        st.divider()
        st.markdown("### 🗂️ Data Sources")
        conn = get_db()
        df = pd.read_sql_query("SELECT source, COUNT(*) as count FROM articles GROUP BY source", conn)
        conn.close()
        for _, row in df.iterrows():
            st.caption(f"• {row['source'].replace('_', ' ').title()}: {row['count']} articles")
