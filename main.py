"""
📰 Professional Newspaper Archive System
Modern, clean UI with professional terminology
Run with: streamlit run streamlit_app.py
"""

import streamlit as st
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timedelta
import time
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ==================== CONFIGURATION ====================
CONFIG_FILE = Path("api_keys.json")

# Page config
st.set_page_config(
    page_title="Newspaper Archive",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded"
)

def load_api_keys() -> Dict:
    """Load API keys from config file"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        'guardian': '',
        'trove': '',
        'nytimes': ''
    }

def save_api_keys(keys: Dict) -> None:
    """Save API keys to config file"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(keys, f)

# ==================== DATABASE SETUP ====================
DATABASE_NAME = 'newspaper_archive.db'

def init_database() -> None:
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

# ==================== DATA SOURCES ====================
class ChroniclingAmericaSource:
    """Chronicling America (Library of Congress) - Free, no API key required"""
    
    SOURCE_NAME = "chronicling_america"
    BASE_URL = "https://chroniclingamerica.loc.gov"
    DISPLAY_NAME = "Chronicling America"
    ICON = "🇺🇸"
    DESCRIPTION = "US historical newspapers (1777-1963)"
    REQUIRES_API_KEY = False
    
    def import_date(self, year: int, month: int, day: int) -> int:
        """Import articles for a specific date"""
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

class GuardianSource:
    """The Guardian Open Platform - Requires API key"""
    
    SOURCE_NAME = "guardian"
    BASE_URL = "https://content.guardianapis.com/search"
    DISPLAY_NAME = "The Guardian"
    ICON = "🇬🇧"
    DESCRIPTION = "Modern news and analysis (1999-present)"
    REQUIRES_API_KEY = True
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
    
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key != '')
    
    def import_search(self, query: str, limit: int = 20) -> Tuple[int, str]:
        """Import articles by search query"""
        if not self.is_configured():
            return 0, "API key required"
        
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

class TroveSource:
    """Trove (National Library of Australia) - Requires API key"""
    
    SOURCE_NAME = "trove"
    BASE_URL = "https://api.trove.nla.gov.au/v3"
    DISPLAY_NAME = "Trove"
    ICON = "🇦🇺"
    DESCRIPTION = "Australian historical newspapers (1803-1954)"
    REQUIRES_API_KEY = True
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
    
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key != '')
    
    def import_search(self, query: str, limit: int = 20) -> Tuple[int, str]:
        """Import articles by search query"""
        if not self.is_configured():
            return 0, "API key required"
        
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
def search_articles(query: str, limit: int = 100) -> List[Dict]:
    """Search articles using FTS5 full-text search"""
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

def get_statistics() -> Dict:
    """Get database statistics"""
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
    """Get articles grouped by source"""
    conn = get_db()
    df = pd.read_sql_query("SELECT source, COUNT(*) as count FROM articles GROUP BY source", conn)
    conn.close()
    return df

# ==================== PROFESSIONAL UI ====================
# Load API keys
api_keys = load_api_keys()

# Initialize database
init_database()

# Professional CSS
st.markdown("""
<style>
    /* Reset and base */
    .stApp {
        background: #f8f9fa;
    }
    
    /* Professional header */
    .professional-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        padding: 2rem 2rem;
        border-radius: 12px;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    .professional-header h1 {
        margin: 0;
        font-size: 2rem;
        font-weight: 600;
        letter-spacing: -0.5px;
    }
    
    .professional-header p {
        margin: 0.5rem 0 0 0;
        opacity: 0.8;
        font-size: 1rem;
    }
    
    /* Statistics cards */
    .stats-container {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 1rem;
        margin-bottom: 2rem;
    }
    
    .stat-card {
        background: white;
        border-radius: 12px;
        padding: 1.25rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        border: 1px solid #e9ecef;
        transition: transform 0.2s, box-shadow 0.2s;
    }
    
    .stat-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
    
    .stat-label {
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: #6c757d;
        font-weight: 600;
        margin-bottom: 0.5rem;
    }
    
    .stat-value {
        font-size: 2rem;
        font-weight: 700;
        color: #1a1a2e;
        line-height: 1;
    }
    
    /* Source cards */
    .source-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 1.5rem;
        margin: 1.5rem 0;
    }
    
    .source-card {
        background: white;
        border-radius: 12px;
        padding: 1.5rem;
        border: 1px solid #e9ecef;
        transition: all 0.3s ease;
    }
    
    .source-card:hover {
        box-shadow: 0 8px 24px rgba(0,0,0,0.1);
        transform: translateY(-4px);
    }
    
    .source-header {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        margin-bottom: 1rem;
    }
    
    .source-icon {
        font-size: 2rem;
    }
    
    .source-title {
        font-size: 1.25rem;
        font-weight: 600;
        color: #1a1a2e;
        margin: 0;
    }
    
    .source-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 20px;
        font-size: 0.7rem;
        font-weight: 600;
        margin-left: 0.5rem;
    }
    
    .badge-free {
        background: #d4edda;
        color: #155724;
    }
    
    .badge-key {
        background: #fff3cd;
        color: #856404;
    }
    
    .badge-active {
        background: #d4edda;
        color: #155724;
    }
    
    .source-description {
        color: #6c757d;
        font-size: 0.9rem;
        margin-bottom: 1rem;
        line-height: 1.5;
    }
    
    /* Search section */
    .search-container {
        background: white;
        border-radius: 12px;
        padding: 2rem;
        margin: 2rem 0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        border: 1px solid #e9ecef;
    }
    
    /* Result cards */
    .result-card {
        background: white;
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        border: 1px solid #e9ecef;
        transition: all 0.2s;
    }
    
    .result-card:hover {
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        border-color: #dee2e6;
    }
    
    .result-title {
        margin: 0 0 0.75rem 0;
        font-size: 1.1rem;
        font-weight: 600;
    }
    
    .result-title a {
        color: #1a1a2e;
        text-decoration: none;
        transition: color 0.2s;
    }
    
    .result-title a:hover {
        color: #4a6cf7;
    }
    
    .result-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 1rem;
        margin-bottom: 0.75rem;
        font-size: 0.8rem;
        color: #6c757d;
    }
    
    .source-tag {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 20px;
        font-size: 0.7rem;
        font-weight: 600;
    }
    
    .source-chronicling_america {
        background: #e7f3ff;
        color: #0066cc;
    }
    
    .source-guardian {
        background: #fee8e6;
        color: #cc3300;
    }
    
    .source-trove {
        background: #e6f3e6;
        color: #008000;
    }
    
    .result-snippet {
        color: #495057;
        line-height: 1.5;
        margin-bottom: 0.75rem;
    }
    
    .result-link {
        font-size: 0.85rem;
    }
    
    .result-link a {
        color: #4a6cf7;
        text-decoration: none;
    }
    
    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: white;
        border-right: 1px solid #e9ecef;
    }
    
    [data-testid="stSidebar"] .stMarkdown {
        color: #495057;
    }
    
    /* Button styling */
    .stButton button {
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.2s;
    }
    
    .stButton button:active {
        transform: scale(0.98);
    }
    
    /* Input styling */
    .stTextInput input, .stSelectbox select, .stMultiselect div {
        border-radius: 8px;
        border: 1px solid #dee2e6;
    }
    
    .stTextInput input:focus {
        border-color: #4a6cf7;
        box-shadow: 0 0 0 2px rgba(74,108,247,0.1);
    }
    
    /* Divider */
    .custom-divider {
        height: 1px;
        background: linear-gradient(to right, transparent, #dee2e6, transparent);
        margin: 2rem 0;
    }
    
    /* Footer */
    .professional-footer {
        text-align: center;
        padding: 2rem;
        color: #6c757d;
        font-size: 0.85rem;
        border-top: 1px solid #e9ecef;
        margin-top: 2rem;
    }
</style>
""", unsafe_allow_html=True)

# ==================== SIDEBAR ====================
with st.sidebar:
    st.markdown("### 📚 Archive Information")
    st.markdown("---")
    
    # Statistics in sidebar
    stats = get_statistics()
    
    st.markdown("#### Collection Stats")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Articles", f"{stats['total_articles']:,}")
    with col2:
        st.metric("Newspapers", f"{stats['newspapers']:,}")
    
    if stats['total_articles'] > 0:
        st.markdown("#### Sources")
        for source in stats['sources']:
            source_name = source['source'].replace('_', ' ').title()
            st.markdown(f"• **{source_name}**: {source['count']:,} articles")
    
    st.markdown("---")
    st.markdown("#### Settings")
    
    with st.expander("API Configuration", expanded=False):
        st.markdown("**Get free API keys:**")
        st.markdown("- [The Guardian](https://open-platform.theguardian.com/access/)")
        st.markdown("- [Trove](https://trove.nla.gov.au/api)")
        
        guardian_key = st.text_input(
            "Guardian API Key",
            value=api_keys['guardian'],
            type="password",
            placeholder="Enter API key",
            key="guardian_key"
        )
        
        trove_key = st.text_input(
            "Trove API Key",
            value=api_keys['trove'],
            type="password",
            placeholder="Enter API key",
            key="trove_key"
        )
        
        if st.button("Save Configuration", use_container_width=True):
            api_keys['guardian'] = guardian_key
            api_keys['trove'] = trove_key
            save_api_keys(api_keys)
            st.success("Configuration saved")
            st.rerun()

# ==================== MAIN CONTENT ====================

# Professional Header
st.markdown("""
<div class="professional-header">
    <h1>📰 Newspaper Archive</h1>
    <p>Access millions of historical and contemporary newspapers from trusted sources</p>
</div>
""", unsafe_allow_html=True)

# Statistics Dashboard
if stats['total_articles'] > 0:
    st.markdown('<div class="stats-container">', unsafe_allow_html=True)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""
        <div class="stat-card">
            <div class="stat-label">Total Articles</div>
            <div class="stat-value">{stats['total_articles']:,}</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="stat-card">
            <div class="stat-label">Newspapers</div>
            <div class="stat-value">{stats['newspapers']:,}</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="stat-card">
            <div class="stat-label">Publication Dates</div>
            <div class="stat-value">{stats['dates']:,}</div>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        st.markdown(f"""
        <div class="stat-card">
            <div class="stat-label">Data Sources</div>
            <div class="stat-value">{len(stats['sources'])}</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown('</div>', unsafe_allow_html=True)

# ==================== DATA SOURCES SECTION ====================
st.markdown("### 📡 Data Sources")
st.markdown("Import content from these trusted newspaper archives:")

# Source cards
sources = [
    {
        "name": "Chronicling America",
        "icon": "🇺🇸",
        "description": "Access historical American newspapers from the Library of Congress collection, covering 1777 to 1963.",
        "badge": "Free Access",
        "badge_class": "badge-free",
        "requires_key": False
    },
    {
        "name": "The Guardian",
        "icon": "🇬🇧",
        "description": "Modern news coverage from one of the UK's leading newspapers, with articles from 1999 to present.",
        "badge": "API Key Required" if not api_keys['guardian'] else "Active",
        "badge_class": "badge-key" if not api_keys['guardian'] else "badge-active",
        "requires_key": True,
        "has_key": api_keys['guardian'] != ''
    },
    {
        "name": "Trove",
        "icon": "🇦🇺",
        "description": "Explore Australian historical newspapers from the National Library of Australia, spanning 1803 to 1954.",
        "badge": "API Key Required" if not api_keys['trove'] else "Active",
        "badge_class": "badge-key" if not api_keys['trove'] else "badge-active",
        "requires_key": True,
        "has_key": api_keys['trove'] != ''
    }
]

st.markdown('<div class="source-grid">', unsafe_allow_html=True)

for source in sources:
    st.markdown(f"""
    <div class="source-card">
        <div class="source-header">
            <span class="source-icon">{source['icon']}</span>
            <h3 class="source-title">{source['name']}</h3>
            <span class="source-badge {source['badge_class']}">{source['badge']}</span>
        </div>
        <div class="source-description">{source['description']}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# ==================== IMPORT CONTROLS ====================
st.markdown("### 📥 Import Content")

tab1, tab2, tab3 = st.tabs(["🇺🇸 US Historical", "🇬🇧 The Guardian", "🇦🇺 Australian"])

with tab1:
    st.markdown("#### Chronicling America")
    st.markdown("Import significant historical events from the Library of Congress collection.")
    
    historical_events = [
        ("Apollo 11 Moon Landing", 1969, 7, 20),
        ("Pearl Harbor Attack", 1941, 12, 7),
        ("End of World War II", 1945, 9, 2),
        ("Titanic Sinking", 1912, 4, 15),
        ("Assassination of JFK", 1963, 11, 22),
        ("First Wright Brothers Flight", 1903, 12, 17),
        ("Stock Market Crash", 1929, 10, 29)
    ]
    
    selected_events = st.multiselect(
        "Select events to import",
        [event[0] for event in historical_events],
        default=["Apollo 11 Moon Landing", "Pearl Harbor Attack"]
    )
    
    if st.button("Import Selected Events", type="primary", use_container_width=True):
        ca_source = ChroniclingAmericaSource()
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        total_imported = 0
        for i, event_name in enumerate(selected_events):
            event = next(e for e in historical_events if e[0] == event_name)
            status_text.text(f"Importing: {event_name}...")
            count = ca_source.import_date(event[1], event[2], event[3])
            total_imported += count
            progress_bar.progress((i + 1) / len(selected_events))
            time.sleep(0.5)
        
        status_text.text(f"✓ Import complete: {total_imported} articles added")
        st.success(f"Successfully imported {total_imported} articles")
        time.sleep(2)
        st.rerun()

with tab2:
    st.markdown("#### The Guardian")
    
    if api_keys['guardian']:
        st.markdown("Import recent articles by topic.")
        
        topics = st.multiselect(
            "Select topics",
            ["Technology", "Science", "Climate", "Politics", "Arts", "Business", "Sports"],
            default=["Technology", "Science"]
        )
        
        article_limit = st.slider("Articles per topic", 5, 50, 20)
        
        if st.button("Import Articles", type="primary", use_container_width=True):
            guardian = GuardianSource(api_keys['guardian'])
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            total_imported = 0
            for i, topic in enumerate(topics):
                status_text.text(f"Importing: {topic}...")
                count, message = guardian.import_search(topic.lower(), article_limit)
                total_imported += count
                progress_bar.progress((i + 1) / len(topics))
                time.sleep(1)
            
            status_text.text(f"✓ Import complete: {total_imported} articles added")
            st.success(f"Successfully imported {total_imported} articles")
            time.sleep(2)
            st.rerun()
    else:
        st.info("🔑 API key required. Configure it in the sidebar to import from The Guardian.")
        st.markdown("[Get your free API key](https://open-platform.theguardian.com/access/)")

with tab3:
    st.markdown("#### Trove")
    
    if api_keys['trove']:
        st.markdown("Import historical Australian newspaper articles.")
        
        topics = st.multiselect(
            "Select topics",
            ["Australian History", "Sydney", "Melbourne", "Gold Rush", "WWI", "Exploration"],
            default=["Australian History"]
        )
        
        article_limit = st.slider("Articles per topic", 5, 50, 20)
        
        if st.button("Import Articles", type="primary", use_container_width=True):
            trove = TroveSource(api_keys['trove'])
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            total_imported = 0
            for i, topic in enumerate(topics):
                status_text.text(f"Importing: {topic}...")
                count, message = trove.import_search(topic.lower(), article_limit)
                total_imported += count
                progress_bar.progress((i + 1) / len(topics))
                time.sleep(1)
            
            status_text.text(f"✓ Import complete: {total_imported} articles added")
            st.success(f"Successfully imported {total_imported} articles")
            time.sleep(2)
            st.rerun()
    else:
        st.info("🔑 API key required. Configure it in the sidebar to import from Trove.")
        st.markdown("[Get your free API key](https://trove.nla.gov.au/api)")

st.markdown('<div class="custom-divider"></div>', unsafe_allow_html=True)

# ==================== SEARCH SECTION ====================
st.markdown("### 🔍 Search Archive")

search_query = st.text_input(
    "",
    placeholder="Enter keywords, names, dates, or topics...",
    label_visibility="collapsed"
)

col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    search_button = st.button("Search", type="primary", use_container_width=True)
with col2:
    if st.button("Browse Recent", use_container_width=True):
        search_query = "the"
        search_button = True

# Results
if search_button and search_query:
    with st.spinner(f"Searching for '{search_query}'..."):
        results = search_articles(search_query, limit=50)
    
    if results:
        st.markdown(f"**Found {len(results)} articles**")
        
        for article in results:
            source_class = f"source-{article['source'].replace('_', '-')}"
            source_display = article['source'].replace('_', ' ').title()
            
            st.markdown(f"""
            <div class="result-card">
                <div class="result-title">
                    <a href="{article['page_url']}" target="_blank">{article['title'][:150]}</a>
                </div>
                <div class="result-meta">
                    <span class="source-tag {source_class}">{source_display}</span>
                    <span>📰 {article['newspaper_name'][:50]}</span>
                    <span>📅 {article['publication_date'] or 'Date unknown'}</span>
                </div>
                <div class="result-snippet">
                    {article['content'][:300] if article['content'] else 'No preview available'}
                </div>
                <div class="result-link">
                    <a href="{article['page_url']}" target="_blank">Read full article →</a>
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No articles found. Try different keywords or import content from the sources above.")

elif stats['total_articles'] == 0:
    st.info("""
    ### Welcome to Newspaper Archive
    
    **Get started:**
    1. Select events or topics from the sections above
    2. Click "Import" to add articles to your archive
    3. Search across all imported content
    
    *Chronicling America is free and requires no API key*
    """)
else:
    # Show recent articles
    st.markdown("#### Recently Added")
    recent = search_articles("a", limit=5)
    if recent:
        for article in recent:
            source_class = f"source-{article['source'].replace('_', '-')}"
            st.markdown(f"""
            <div class="result-card">
                <div class="result-title">
                    <a href="{article['page_url']}" target="_blank">{article['title'][:100]}</a>
                </div>
                <div class="result-meta">
                    <span class="source-tag {source_class}">{article['source'].replace('_', ' ').title()}</span>
                    <span>📅 {article['publication_date'] or 'Date unknown'}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

# Footer
st.markdown("""
<div class="professional-footer">
    <p>Newspaper Archive • Powered by Library of Congress, The Guardian, and Trove</p>
    <p style="font-size: 0.75rem; margin-top: 0.5rem;">All articles link to original sources. Respect copyright and terms of service.</p>
</div>
""", unsafe_allow_html=True)
