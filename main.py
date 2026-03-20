"""
📰 Free Newspaper Archive - Mobile Optimized Version
Run with: streamlit run streamlit_app.py
"""

import streamlit as st
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import os
import json
from pathlib import Path

# ==================== CONFIGURATION ====================
CONFIG_FILE = Path("api_keys.json")

# Mobile detection and optimization
def is_mobile():
    """Detect if the user is on mobile"""
    try:
        # Check screen width via JavaScript (set in custom CSS)
        if 'mobile_detected' not in st.session_state:
            st.session_state.mobile_detected = False
        return st.session_state.mobile_detected
    except:
        return False

def load_api_keys():
    """Load API keys from config file"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        'guardian': '',
        'trove': '',
        'nytimes': '',
        'washington_post': ''
    }

def save_api_keys(keys):
    """Save API keys to config file"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(keys, f)

# ==================== DATABASE SETUP ====================
DATABASE_NAME = 'newspaper_archive.db'

def init_database():
    """Initialize the SQLite database with full-text search support"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cursor.execute('PRAGMA journal_mode=WAL')
    
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
            api_url TEXT,
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
    
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid, title, content, newspaper_name, source, publication_date)
            VALUES (new.id, new.title, new.content, new.newspaper_name, new.source, new.publication_date);
        END
    ''')
    
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, content, newspaper_name, source, publication_date)
            VALUES ('delete', old.id, old.title, old.content, old.newspaper_name, old.source, old.publication_date);
        END
    ''')
    
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, content, newspaper_name, source, publication_date)
            VALUES ('delete', old.id, old.title, old.content, old.newspaper_name, old.source, old.publication_date);
            INSERT INTO articles_fts(rowid, title, content, newspaper_name, source, publication_date)
            VALUES (new.id, new.title, new.content, new.newspaper_name, new.source, new.publication_date);
        END
    ''')
    
    conn.commit()
    conn.close()

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== HARVESTERS ====================
class ChroniclingAmericaHarvester:
    SOURCE_NAME = "chronicling_america"
    BASE_URL = "https://chroniclingamerica.loc.gov"
    
    def harvest_date(self, year, month, day):
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        search_url = f"{self.BASE_URL}/search/pages/results/?date1={date_str}&date2={date_str}&format=json&rows=100"
        
        try:
            response = requests.get(search_url, timeout=10)
            response.raise_for_status()
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
                api_url = f"{self.BASE_URL}{external_id}.json"
                
                if content:
                    cursor.execute('''
                        INSERT INTO articles 
                        (source, external_id, title, newspaper_name, publication_date, 
                         content, page_url, api_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (self.SOURCE_NAME, external_id, title, newspaper, date_str, 
                          content[:5000], page_url, api_url))
                    stored += 1
            
            conn.commit()
            conn.close()
            return stored
            
        except Exception as e:
            return 0

class GuardianHarvester:
    SOURCE_NAME = "guardian"
    BASE_URL = "https://content.guardianapis.com/search"
    
    def __init__(self, api_key=None):
        self.api_key = api_key
    
    def is_configured(self):
        return bool(self.api_key and self.api_key != '')
    
    def harvest_search(self, query, limit=20):
        if not self.is_configured():
            return 0, "API key not configured"
        
        params = {
            'api-key': self.api_key,
            'q': query,
            'format': 'json',
            'page-size': min(limit, 200),
            'show-fields': 'bodyText'
        }
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            
            if response.status_code == 401:
                return 0, "Invalid API key"
            
            response.raise_for_status()
            data = response.json()
            
            articles = data.get('response', {}).get('results', [])
            if not articles:
                return 0, "No articles found"
            
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
                newspaper = 'The Guardian'
                date = article.get('webPublicationDate', '')[:10]
                content = article.get('fields', {}).get('bodyText', '')
                page_url = article.get('webUrl', '')
                api_url = article.get('apiUrl', '')
                
                if content:
                    cursor.execute('''
                        INSERT INTO articles 
                        (source, external_id, title, newspaper_name, publication_date, 
                         content, page_url, api_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (self.SOURCE_NAME, external_id, title, newspaper, date, 
                          content[:5000], page_url, api_url))
                    stored += 1
            
            conn.commit()
            conn.close()
            return stored, "Success"
            
        except Exception as e:
            return 0, f"Error: {str(e)}"

class TroveHarvester:
    SOURCE_NAME = "trove"
    BASE_URL = "https://api.trove.nla.gov.au/v3"
    
    def __init__(self, api_key=None):
        self.api_key = api_key
    
    def is_configured(self):
        return bool(self.api_key and self.api_key != '')
    
    def harvest_search(self, query, limit=20):
        if not self.is_configured():
            return 0, "API key not configured"
        
        params = {
            'apiKey': self.api_key,
            'q': query,
            'category': 'newspaper',
            'encoding': 'json',
            'n': limit
        }
        
        try:
            response = requests.get(f"{self.BASE_URL}/result", params=params, timeout=10)
            
            if response.status_code == 403:
                return 0, "Invalid API key"
            
            response.raise_for_status()
            data = response.json()
            
            articles = data.get('response', {}).get('zone', [{}])[0].get('records', {}).get('article', [])
            if not articles:
                return 0, "No articles found"
            
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
                
                title = article.get('title', 'Untitled')
                newspaper = article.get('newspaper', {}).get('title', 'Unknown')
                date = article.get('date', '')
                content = article.get('heading', '') + ' ' + article.get('text', '')
                page_url = article.get('url', '')
                api_url = f"https://trove.nla.gov.au/newspaper/article/{external_id}"
                
                cursor.execute('''
                    INSERT INTO articles 
                    (source, external_id, title, newspaper_name, publication_date, 
                     content, page_url, api_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (self.SOURCE_NAME, external_id, title, newspaper, date, 
                      content[:5000], page_url, api_url))
                stored += 1
            
            conn.commit()
            conn.close()
            return stored, "Success"
            
        except Exception as e:
            return 0, f"Error: {str(e)}"

# ==================== SEARCH ENGINE ====================
def search_articles(query, limit=100):
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT a.id, a.source, a.title, a.newspaper_name, 
                   a.publication_date, a.content, a.page_url
            FROM articles a
            JOIN articles_fts f ON a.id = f.rowid
            WHERE articles_fts MATCH ?
            ORDER BY 
                CASE 
                    WHEN f.title MATCH ? THEN 3
                    WHEN f.newspaper_name MATCH ? THEN 2
                    ELSE 1
                END DESC,
                a.publication_date DESC
            LIMIT ?
        ''', (query, query, query, limit))
        
        results = [dict(row) for row in cursor.fetchall()]
        
        for result in results:
            if result['content'] and len(result['content']) > 300:
                result['content'] = result['content'][:300] + '...'
        
        return results
    except Exception as e:
        return []
    finally:
        conn.close()

def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) as total FROM articles')
    total = cursor.fetchone()['total']
    
    cursor.execute('SELECT source, COUNT(*) as count FROM articles GROUP BY source')
    sources = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute('SELECT COUNT(DISTINCT newspaper_name) as newspapers FROM articles')
    newspapers = cursor.fetchone()['newspapers']
    
    cursor.execute('SELECT COUNT(DISTINCT publication_date) as dates FROM articles WHERE publication_date IS NOT NULL')
    dates = cursor.fetchone()['dates']
    
    conn.close()
    
    return {
        'total_articles': total,
        'newspapers': newspapers,
        'dates': dates,
        'sources': sources
    }

def get_articles_by_source():
    conn = get_db()
    df = pd.read_sql_query("SELECT source, COUNT(*) as count FROM articles GROUP BY source", conn)
    conn.close()
    return df

def get_articles_by_year():
    conn = get_db()
    df = pd.read_sql_query("""
        SELECT 
            SUBSTR(publication_date, 1, 4) as year,
            COUNT(*) as count
        FROM articles 
        WHERE publication_date IS NOT NULL AND publication_date != ''
        GROUP BY year
        ORDER BY year
    """, conn)
    conn.close()
    return df

# ==================== MOBILE-OPTIMIZED STREAMLIT UI ====================
st.set_page_config(
    page_title="Free Newspaper Archive",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="auto"  # Auto-collapse on mobile
)

# Mobile detection via custom CSS
st.markdown("""
    <script>
        // Detect mobile device and set session state
        const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
        if (isMobile) {
            document.body.classList.add('mobile-device');
        }
    </script>
""", unsafe_allow_html=True)

# Load API keys
api_keys = load_api_keys()

# Mobile-optimized CSS
st.markdown("""
    <style>
    /* Global mobile-first styles */
    * {
        -webkit-tap-highlight-color: transparent;
    }
    
    .stApp {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    }
    
    /* Make buttons larger for touch on mobile */
    @media (max-width: 768px) {
        button, .stButton button {
            min-height: 48px !important;
            font-size: 16px !important;
            padding: 12px 20px !important;
        }
        
        /* Larger touch targets for inputs */
        input, textarea, .stTextInput input {
            min-height: 48px !important;
            font-size: 16px !important;
        }
        
        /* Better spacing for mobile */
        .block-container {
            padding: 1rem 0.8rem !important;
        }
        
        /* Adjust column spacing */
        .row-widget.stHorizontal {
            gap: 0.5rem !important;
        }
        
        /* Make cards easier to tap */
        .result-card {
            padding: 1rem !important;
            margin-bottom: 0.8rem !important;
        }
        
        /* Larger clickable areas for links */
        a {
            display: inline-block;
            padding: 4px 0;
        }
        
        /* Adjust sidebar for mobile */
        [data-testid="stSidebar"] {
            min-width: 280px !important;
        }
        
        /* Better touch scrolling */
        .stMarkdown {
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }
        
        /* Larger font for readability on mobile */
        body, p, li, .stMarkdown {
            font-size: 16px !important;
            line-height: 1.5 !important;
        }
        
        /* Headers smaller on mobile */
        h1 {
            font-size: 1.8rem !important;
        }
        h2 {
            font-size: 1.4rem !important;
        }
        h3 {
            font-size: 1.2rem !important;
        }
    }
    
    /* Desktop styles */
    @media (min-width: 769px) {
        .result-card {
            padding: 1.5rem !important;
            margin-bottom: 1rem !important;
        }
    }
    
    /* Header styling */
    .main-header {
        text-align: center;
        color: white;
        padding: 1rem;
        border-radius: 10px;
        margin-bottom: 1.5rem;
    }
    
    .main-header h1 {
        margin: 0;
        font-size: 2rem;
    }
    
    .main-header p {
        margin: 0.5rem 0 0 0;
        opacity: 0.9;
    }
    
    /* Stat cards - responsive grid */
    .stats-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 1rem;
        margin: 1rem 0;
    }
    
    .stat-card {
        background: rgba(255,255,255,0.2);
        backdrop-filter: blur(10px);
        border-radius: 15px;
        padding: 1rem;
        text-align: center;
        color: white;
    }
    
    .stat-number {
        font-size: 1.8rem;
        font-weight: bold;
        margin: 0.5rem 0;
    }
    
    .stat-label {
        font-size: 0.85rem;
        opacity: 0.9;
    }
    
    /* Result cards */
    .result-card {
        background: white;
        border-radius: 12px;
        padding: 1rem;
        margin-bottom: 0.8rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        transition: transform 0.2s;
        word-wrap: break-word;
    }
    
    .result-card:active {
        transform: scale(0.98);
    }
    
    .result-card h3, .result-card h4 {
        margin: 0 0 0.5rem 0;
        font-size: 1.1rem;
    }
    
    .result-card h3 a, .result-card h4 a {
        color: #333;
        text-decoration: none;
    }
    
    .result-meta {
        font-size: 0.75rem;
        color: #666;
        margin: 0.5rem 0;
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        align-items: center;
    }
    
    .source-badge {
        display: inline-block;
        padding: 0.2rem 0.5rem;
        border-radius: 12px;
        font-size: 0.7rem;
        font-weight: bold;
    }
    
    .source-chronicling_america {
        background: #e3f2fd;
        color: #1976d2;
    }
    
    .source-guardian {
        background: #fef3e8;
        color: #f57c00;
    }
    
    .source-trove {
        background: #e8f5e9;
        color: #388e3c;
    }
    
    .result-snippet {
        font-size: 0.9rem;
        color: #555;
        line-height: 1.4;
        margin-top: 0.5rem;
    }
    
    /* Harvest cards */
    .harvest-card {
        background: rgba(255,255,255,0.95);
        border-radius: 12px;
        padding: 1rem;
        height: 100%;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    
    .harvest-card h4 {
        margin: 0 0 0.5rem 0;
        font-size: 1.2rem;
    }
    
    /* API status badges */
    .api-status {
        display: inline-block;
        padding: 0.2rem 0.5rem;
        border-radius: 20px;
        font-size: 0.7rem;
        font-weight: bold;
        margin-left: 0.5rem;
    }
    
    .api-active {
        background: #4caf50;
        color: white;
    }
    
    .api-inactive {
        background: #f44336;
        color: white;
    }
    
    /* Mobile bottom padding for easier scrolling */
    .stApp {
        padding-bottom: 20px;
    }
    
    /* Toast-like messages for mobile */
    .stAlert {
        border-radius: 12px;
        margin: 0.5rem 0;
    }
    </style>
""", unsafe_allow_html=True)

# Initialize database
init_database()

# ==================== MOBILE-FRIENDLY LAYOUT ====================
# Use tabs instead of sidebar for mobile? Let's keep sidebar but make it collapsible
with st.sidebar:
    # Collapsible header for mobile
    st.markdown("""
        <div style="text-align: center;">
            <img src="https://img.icons8.com/fluency/96/newspaper.png" width="60">
            <h3 style="margin: 0.5rem 0;">📰 Newspaper Archive</h3>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # ==================== API KEY MANAGEMENT ====================
    with st.expander("🔑 Configure API Keys", expanded=False):
        st.markdown("""
        **Get free API keys:**
        - 🗞️ [The Guardian](https://open-platform.theguardian.com/access/)
        - 🇦🇺 [Trove](https://trove.nla.gov.au/api)
        """)
        
        guardian_key = st.text_input(
            "Guardian API Key",
            value=api_keys['guardian'],
            type="password",
            placeholder="Enter your key",
            key="guardian_key_input"
        )
        
        trove_key = st.text_input(
            "Trove API Key",
            value=api_keys['trove'],
            type="password",
            placeholder="Enter your key",
            key="trove_key_input"
        )
        
        if st.button("💾 Save Keys", use_container_width=True):
            api_keys['guardian'] = guardian_key
            api_keys['trove'] = trove_key
            save_api_keys(api_keys)
            st.success("✅ Keys saved!")
            st.rerun()
    
    # API Status
    st.markdown("### 📡 Status")
    
    # Chronicling America (always free)
    st.markdown("**Chronicling America** ✅ Free")
    
    # Guardian status
    if api_keys['guardian']:
        st.markdown("**The Guardian** ✅ Active")
    else:
        st.markdown("**The Guardian** ⚠️ Add key")
    
    # Trove status
    if api_keys['trove']:
        st.markdown("**Trove** ✅ Active")
    else:
        st.markdown("**Trove** ⚠️ Add key")
    
    st.markdown("---")
    
    # Stats in compact form for mobile
    stats = get_stats()
    st.markdown("### 📊 Stats")
    
    # Use columns for compact stats
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Articles", f"{stats['total_articles']:,}")
    with col2:
        st.metric("Newspapers", f"{stats['newspapers']:,}")
    
    if stats['total_articles'] > 0:
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Dates", f"{stats['dates']:,}")
        with col2:
            st.metric("Sources", f"{len(stats['sources'])}")

# Main content
st.markdown("""
<div class="main-header">
    <h1>📰 Free Newspaper Archive</h1>
    <p>Search millions of historical and modern newspapers</p>
</div>
""", unsafe_allow_html=True)

# ==================== HARVESTING SECTION (MOBILE-OPTIMIZED) ====================
st.subheader("🌾 Harvest Articles")

# Use columns that stack on mobile
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("""
    <div class="harvest-card">
        <h4>🇺🇸 Chronicling America</h4>
        <p style="font-size: 0.85rem;">Free • No key needed</p>
        <p>Historical US newspapers (1777-1963)</p>
    </div>
    """, unsafe_allow_html=True)
    
    if st.button("📜 Harvest Historical Events", key="harvest_ca", use_container_width=True):
        with st.spinner("Harvesting..."):
            ca = ChroniclingAmericaHarvester()
            
            historical_dates = [
                ("Moon Landing", 1969, 7, 20),
                ("Pearl Harbor", 1941, 12, 7),
                ("WWII Ends", 1945, 9, 2),
                ("Titanic Sinks", 1912, 4, 15),
                ("JFK Assassination", 1963, 11, 22)
            ]
            
            progress_bar = st.progress(0)
            status = st.empty()
            total = 0
            
            for i, (event, year, month, day) in enumerate(historical_dates):
                status.text(f"{event}...")
                count = ca.harvest_date(year, month, day)
                total += count
                progress_bar.progress((i + 1) / len(historical_dates))
                time.sleep(0.5)
            
            status.text(f"✅ Added {total} articles!")
            st.success(f"Added {total} historical articles")
            time.sleep(1.5)
            st.rerun()

with col2:
    st.markdown("""
    <div class="harvest-card">
        <h4>🇬🇧 The Guardian</h4>
        <p style="font-size: 0.85rem;">Requires API key</p>
        <p>Modern news (1999-present)</p>
    </div>
    """, unsafe_allow_html=True)
    
    if api_keys['guardian']:
        # Compact topic selection for mobile
        topics = st.multiselect(
            "Topics",
            ["tech", "science", "climate", "politics"],
            default=["tech", "science"],
            key="guardian_topics"
        )
        
        limit = st.selectbox("Articles", [10, 20, 30], index=1, key="guardian_limit")
        
        if st.button("🌾 Harvest Guardian", key="harvest_guardian", use_container_width=True):
            guardian = GuardianHarvester(api_keys['guardian'])
            with st.spinner("Harvesting..."):
                progress = st.progress(0)
                total = 0
                for i, topic in enumerate(topics):
                    count, msg = guardian.harvest_search(topic, limit=int(limit))
                    total += count
                    progress.progress((i + 1) / len(topics))
                    time.sleep(1)
                st.success(f"✅ Added {total} articles!")
                time.sleep(1.5)
                st.rerun()
    else:
        st.info("Add API key in sidebar", icon="🔑")

with col3:
    st.markdown("""
    <div class="harvest-card">
        <h4>🇦🇺 Trove</h4>
        <p style="font-size: 0.85rem;">Requires API key</p>
        <p>Australian newspapers (1803-1954)</p>
    </div>
    """, unsafe_allow_html=True)
    
    if api_keys['trove']:
        topics = st.multiselect(
            "Topics",
            ["history", "australia", "sydney"],
            default=["history"],
            key="trove_topics"
        )
        
        limit = st.selectbox("Articles", [10, 20, 30], index=1, key="trove_limit")
        
        if st.button("🌾 Harvest Trove", key="harvest_trove", use_container_width=True):
            trove = TroveHarvester(api_keys['trove'])
            with st.spinner("Harvesting..."):
                progress = st.progress(0)
                total = 0
                for i, topic in enumerate(topics):
                    count, msg = trove.harvest_search(topic, limit=int(limit))
                    total += count
                    progress.progress((i + 1) / len(topics))
                    time.sleep(1)
                st.success(f"✅ Added {total} articles!")
                time.sleep(1.5)
                st.rerun()
    else:
        st.info("Add API key in sidebar", icon="🔑")

st.markdown("---")

# ==================== SEARCH SECTION (MOBILE-OPTIMIZED) ====================
st.subheader("🔍 Search Archive")

# Mobile-friendly search
search_query = st.text_input(
    "",
    placeholder="Search articles, events, names...",
    key="search_input"
)

# Search buttons row - responsive
search_col1, search_col2 = st.columns(2)
with search_col1:
    search_button = st.button("🔍 Search", type="primary", use_container_width=True)
with search_col2:
    if st.button("🎲 Random", use_container_width=True):
        search_query = "the"
        search_button = True

# Results
if search_button and search_query:
    with st.spinner(f"Searching..."):
        results = search_articles(search_query, limit=50)
    
    if results:
        st.success(f"Found {len(results)} results")
        
        for article in results:
            source_class = f"source-{article['source'].replace('_', '-')}"
            st.markdown(f"""
            <div class="result-card">
                <h3><a href="{article['page_url']}" target="_blank">{article['title'][:100]}</a></h3>
                <div class="result-meta">
                    <span class="source-badge {source_class}">{article['source'].replace('_', ' ').upper()}</span>
                    <span>📰 {article['newspaper_name'][:30]}</span>
                    <span>📅 {article['publication_date'] or 'Unknown'}</span>
                </div>
                <div class="result-snippet">{article['content'][:200] if article['content'] else 'No preview'}</div>
                <small><a href="{article['page_url']}" target="_blank">Read full article →</a></small>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.warning("No results found. Try different keywords or harvest more articles.")

elif stats['total_articles'] == 0:
    st.info("""
    ### 👋 Welcome!
    
    **Get started:**
    1. Click **"Harvest Historical Events"** above
    2. Or add API keys in sidebar for more content
    3. Search your archive!
    
    *No API key needed for US historical newspapers*
    """)
else:
    # Show recent articles
    st.subheader("📰 Recently Added")
    recent = search_articles("a", limit=5)
    if recent:
        for article in recent:
            source_class = f"source-{article['source'].replace('_', '-')}"
            st.markdown(f"""
            <div class="result-card">
                <h4><a href="{article['page_url']}" target="_blank">{article['title'][:80]}</a></h4>
                <div class="result-meta">
                    <span class="source-badge {source_class}">{article['source'].replace('_', ' ').upper()}</span>
                    <span>📅 {article['publication_date']}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

# Footer (compact for mobile)
st.markdown("""
<div style="text-align: center; padding: 1.5rem; font-size: 0.75rem; color: rgba(255,255,255,0.8);">
    <p>📚 Sources: Chronicling America (LOC) • The Guardian • Trove (NLA)</p>
    <p>🔗 All articles link to original sources</p>
</div>
""", unsafe_allow_html=True)
