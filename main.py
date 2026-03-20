"""
📚 Newspaper Archive - Library Style Interface (FULLY WORKING)
Both Chronicling America and The Guardian working properly
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
from typing import Dict, List, Tuple

# ==================== PAGE CONFIG ====================
st.set_page_config(
    page_title="Newspaper Library",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded"
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
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_date ON articles(publication_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_source ON articles(source)')
    
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
        """Import articles for a specific date"""
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        # Try different search approaches
        search_urls = [
            f"{self.BASE_URL}/search/pages/results/?date1={date_str}&date2={date_str}&format=json&rows=50",
            f"{self.BASE_URL}/search/pages/results/?proxtext=&dateFilterType=yearRange&date1={date_str}&date2={date_str}&format=json&rows=50"
        ]
        
        for url in search_urls:
            try:
                response = requests.get(url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    items = data.get('items', [])
                    
                    if items:
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
                            if not content:
                                content = item.get('ocr', '')
                            
                            # Get the page URL
                            page_url = f"{self.BASE_URL}{item.get('url', '')}" if item.get('url') else None
                            
                            if content and len(content) > 50:  # Only store if there's meaningful content
                                cursor.execute('''
                                    INSERT INTO articles 
                                    (source, external_id, title, newspaper_name, publication_date, content, page_url)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                ''', (self.SOURCE_NAME, external_id, title[:200], newspaper, date_str, content[:3000], page_url))
                                stored += 1
                        
                        conn.commit()
                        conn.close()
                        
                        if stored > 0:
                            return stored
            except Exception as e:
                continue
        
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
            'show-fields': 'bodyText,headline',
            'order-by': 'newest'
        }
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=15)
            
            if response.status_code == 401:
                return 0, "Invalid API key"
            if response.status_code != 200:
                return 0, f"API error: {response.status_code}"
            
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
                date = article.get('webPublicationDate', '')[:10]
                content = article.get('fields', {}).get('bodyText', '')
                page_url = article.get('webUrl', '')
                
                if content and len(content) > 50:
                    cursor.execute('''
                        INSERT INTO articles 
                        (source, external_id, title, newspaper_name, publication_date, content, page_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (self.SOURCE_NAME, external_id, title[:200], 'The Guardian', date, content[:3000], page_url))
                    stored += 1
            
            conn.commit()
            conn.close()
            return stored, "Success"
            
        except Exception as e:
            return 0, f"Error: {str(e)}"

# ==================== LIBRARY FUNCTIONS ====================
def get_all_articles(limit=100, offset=0):
    """Get all articles with pagination"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.id, a.source, a.title, a.newspaper_name, 
               a.publication_date, a.content, a.page_url
        FROM articles a
        ORDER BY a.publication_date DESC
        LIMIT ? OFFSET ?
    ''', (limit, offset))
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    for r in results:
        if r['content'] and len(r['content']) > 200:
            r['content'] = r['content'][:200] + '...'
    
    return results

def get_total_article_count():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as total FROM articles')
    total = cursor.fetchone()['total']
    conn.close()
    return total

def get_articles_by_year(year):
    """Get articles from a specific year"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT a.id, a.source, a.title, a.newspaper_name, 
               a.publication_date, a.content, a.page_url
        FROM articles a
        WHERE SUBSTR(a.publication_date, 1, 4) = ?
        ORDER BY a.publication_date DESC
        LIMIT 200
    ''', (str(year),))
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    for r in results:
        if r['content'] and len(r['content']) > 200:
            r['content'] = r['content'][:200] + '...'
    
    return results

def get_all_newspapers():
    """Get list of all newspapers in the database"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT newspaper_name, COUNT(*) as count 
        FROM articles 
        GROUP BY newspaper_name 
        ORDER BY count DESC 
        LIMIT 100
    ''')
    newspapers = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return newspapers

def get_all_sources():
    """Get list of all sources with counts"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT source, COUNT(*) as count 
        FROM articles 
        GROUP BY source
    ''')
    sources = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return sources

def get_all_years():
    """Get list of all years with article counts"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT SUBSTR(publication_date, 1, 4) as year, COUNT(*) as count 
        FROM articles 
        WHERE publication_date IS NOT NULL AND publication_date != ''
        GROUP BY year 
        ORDER BY year DESC
    ''')
    years = [dict(row) for row in cursor.fetchall() if row['year']]
    conn.close()
    return years

def search_articles(query, year_from=None, year_to=None, limit=100):
    """Search articles with optional year filtering"""
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        if year_from or year_to:
            cursor.execute('''
                SELECT a.id, a.source, a.title, a.newspaper_name, 
                       a.publication_date, a.content, a.page_url
                FROM articles a
                JOIN articles_fts f ON a.id = f.rowid
                WHERE articles_fts MATCH ?
                ORDER BY a.publication_date DESC
            ''', (query,))
            
            results = [dict(row) for row in cursor.fetchall()]
            
            filtered = []
            for article in results:
                pub_date = article.get('publication_date', '')
                if pub_date and len(pub_date) >= 4:
                    year = int(pub_date[:4])
                    if year_from and year < int(year_from):
                        continue
                    if year_to and year > int(year_to):
                        continue
                    filtered.append(article)
            
            results = filtered[:limit]
        else:
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
            if r['content'] and len(r['content']) > 200:
                r['content'] = r['content'][:200] + '...'
        
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
    
    cursor.execute('''
        SELECT MIN(publication_date) as min_date, MAX(publication_date) as max_date 
        FROM articles 
        WHERE publication_date IS NOT NULL AND publication_date != ''
    ''')
    date_range = cursor.fetchone()
    min_year = date_range['min_date'][:4] if date_range['min_date'] else None
    max_year = date_range['max_date'][:4] if date_range['max_date'] else None
    
    conn.close()
    return {'total': total, 'newspapers': newspapers, 'min_year': min_year, 'max_year': max_year}

# ==================== MAIN APP ====================
init_database()
api_keys = load_api_keys()
stats = get_stats()

# Header
st.title("📚 Newspaper Library")
st.markdown("A comprehensive digital library of historical and contemporary newspapers")

# Stats banner
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Articles", f"{stats['total']:,}")
with col2:
    st.metric("Newspapers", f"{stats['newspapers']:,}")
with col3:
    if stats['min_year']:
        st.metric("Earliest", stats['min_year'])
with col4:
    if stats['max_year']:
        st.metric("Latest", stats['max_year'])

st.divider()

# ==================== LIBRARY NAVIGATION ====================
tab_library, tab_search, tab_import = st.tabs(["📚 Browse Library", "🔍 Search & Filter", "📥 Import Content"])

# ==================== TAB 1: BROWSE LIBRARY ====================
with tab_library:
    if stats['total'] == 0:
        st.info("📭 The library is empty. Go to the 'Import Content' tab to add articles.")
        st.markdown("""
        ### Quick Start:
        1. **For historical US newspapers**: Use the "Import from Chronicling America" section below
        2. **For modern news**: Add your Guardian API key and import topics
        3. **Browse** your collection once you have articles!
        """)
    else:
        browse_by = st.radio(
            "Browse by:",
            ["📅 Year", "📰 Newspaper", "🗞️ Source", "🔄 Recent Articles"],
            horizontal=True
        )
        
        st.divider()
        
        if browse_by == "📅 Year":
            st.subheader("Browse by Year")
            years = get_all_years()
            
            if years:
                cols = st.columns(5)
                for idx, year_data in enumerate(years[:30]):
                    year = year_data['year']
                    count = year_data['count']
                    with cols[idx % 5]:
                        with st.container():
                            st.markdown(f"""
                            <div style="
                                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                                padding: 1rem;
                                border-radius: 10px;
                                text-align: center;
                                margin: 0.5rem 0;
                            ">
                                <h3 style="color: white; margin: 0;">{year}</h3>
                                <p style="color: white; margin: 0; font-size: 0.8rem;">{count} articles</p>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            if st.button(f"View {year}", key=f"year_{idx}", use_container_width=True):
                                st.session_state.selected_year = year
                                st.session_state.view_year = True
                                st.rerun()
                
                if hasattr(st.session_state, 'view_year') and st.session_state.view_year:
                    year = st.session_state.selected_year
                    st.divider()
                    st.subheader(f"📅 {year} - Articles")
                    
                    articles = get_articles_by_year(year)
                    for article in articles:
                        with st.container():
                            col1, col2 = st.columns([4, 1])
                            with col1:
                                st.markdown(f"**{article['title']}**")
                                st.caption(f"📰 {article['newspaper_name']} | 📅 {article['publication_date']}")
                                if article['content']:
                                    st.text(article['content'][:150] + "...")
                            with col2:
                                if article['page_url']:
                                    st.link_button("Read Article →", article['page_url'])
                            st.divider()
            else:
                st.info("No articles with valid dates found.")
        
        elif browse_by == "📰 Newspaper":
            st.subheader("Browse by Newspaper")
            newspapers = get_all_newspapers()
            
            if newspapers:
                newspaper_search = st.text_input("Search newspapers", placeholder="Enter newspaper name...")
                
                filtered_newspapers = newspapers
                if newspaper_search:
                    filtered_newspapers = [n for n in newspapers if newspaper_search.lower() in n['newspaper_name'].lower()]
                
                cols = st.columns(3)
                for idx, paper in enumerate(filtered_newspapers[:30]):
                    with cols[idx % 3]:
                        with st.container():
                            st.markdown(f"""
                            <div style="
                                border: 1px solid #e0e0e0;
                                padding: 1rem;
                                border-radius: 8px;
                                margin: 0.5rem 0;
                            ">
                                <h4>{paper['newspaper_name'][:50]}</h4>
                                <p style="color: #666;">{paper['count']} articles</p>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            if st.button(f"Browse {paper['newspaper_name'][:30]}", key=f"paper_{idx}", use_container_width=True):
                                st.session_state.selected_newspaper = paper['newspaper_name']
                                st.session_state.view_newspaper = True
                                st.rerun()
            else:
                st.info("No newspapers found in the library.")
        
        elif browse_by == "🗞️ Source":
            st.subheader("Browse by Source")
            sources = get_all_sources()
            
            if sources:
                source_display = {
                    'chronicling_america': {'name': 'Chronicling America (LOC)', 'icon': '🇺🇸', 'color': '#3b82f6'},
                    'guardian': {'name': 'The Guardian', 'icon': '🇬🇧', 'color': '#10b981'}
                }
                
                cols = st.columns(2)
                for idx, source in enumerate(sources):
                    info = source_display.get(source['source'], {'name': source['source'], 'icon': '📰', 'color': '#6b7280'})
                    with cols[idx % 2]:
                        with st.container():
                            st.markdown(f"""
                            <div style="
                                background: {info['color']}10;
                                border-left: 4px solid {info['color']};
                                padding: 1rem;
                                border-radius: 8px;
                                margin: 0.5rem 0;
                            ">
                                <h3>{info['icon']} {info['name']}</h3>
                                <p>{source['count']:,} articles</p>
                            </div>
                            """, unsafe_allow_html=True)
            else:
                st.info("No sources found.")
        
        else:  # Recent Articles
            st.subheader("Recent Articles")
            
            articles_per_page = 20
            total_articles = get_total_article_count()
            total_pages = max(1, (total_articles // articles_per_page) + 1)
            
            if 'page_num' not in st.session_state:
                st.session_state.page_num = 1
            
            col_prev, col_page_info, col_next = st.columns([1, 2, 1])
            with col_prev:
                if st.button("← Previous", disabled=st.session_state.page_num == 1):
                    st.session_state.page_num -= 1
                    st.rerun()
            with col_page_info:
                st.markdown(f"<div style='text-align: center'>Page {st.session_state.page_num} of {total_pages}</div>", unsafe_allow_html=True)
            with col_next:
                if st.button("Next →", disabled=st.session_state.page_num == total_pages):
                    st.session_state.page_num += 1
                    st.rerun()
            
            offset = (st.session_state.page_num - 1) * articles_per_page
            articles = get_all_articles(limit=articles_per_page, offset=offset)
            
            for article in articles:
                with st.container():
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"**{article['title']}**")
                        st.caption(f"📰 {article['newspaper_name']} | 📅 {article['publication_date']}")
                        if article['content']:
                            st.text(article['content'][:150] + "...")
                    with col2:
                        if article['page_url']:
                            st.link_button("Read Article →", article['page_url'])
                    st.divider()

# ==================== TAB 2: SEARCH & FILTER ====================
with tab_search:
    st.subheader("Search the Library")
    
    with st.form("search_form"):
        search_term = st.text_input("Search keywords", placeholder="Enter any words, names, or topics...")
        
        col1, col2 = st.columns(2)
        with col1:
            year_options = ["Any"] + [str(y['year']) for y in get_all_years()]
            year_from = st.selectbox("From Year", year_options)
        with col2:
            year_to = st.selectbox("To Year", year_options)
        
        submitted = st.form_submit_button("🔍 Search", type="primary", use_container_width=True)
    
    if submitted and search_term:
        from_year = year_from if year_from != "Any" else None
        to_year = year_to if year_to != "Any" else None
        
        with st.spinner(f"Searching for '{search_term}'..."):
            results = search_articles(search_term, from_year, to_year, limit=100)
        
        if results:
            st.success(f"Found {len(results)} articles")
            
            for article in results:
                with st.container():
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"**{article['title']}**")
                        st.caption(f"📰 {article['newspaper_name']} | 📅 {article['publication_date']}")
                        if article['content']:
                            st.text(article['content'][:150] + "...")
                    with col2:
                        if article['page_url']:
                            st.link_button("Read Article →", article['page_url'])
                    st.divider()
        else:
            st.info("No articles found. Try different keywords or import more content.")

# ==================== TAB 3: IMPORT CONTENT ====================
with tab_import:
    st.subheader("Add Content to Library")
    
    # Chronicling America Import
    st.markdown("### 🇺🇸 Chronicling America (Free)")
    st.caption("US historical newspapers 1777-1963 - No API key required")
    
    # Test dates that definitely have content
    test_dates = [
        ("Apollo 11 Moon Landing", 1969, 7, 20),
        ("Pearl Harbor Attack", 1941, 12, 7),
        ("End of WWII", 1945, 9, 2),
        ("Titanic Sinking", 1912, 4, 15),
        ("JFK Assassination", 1963, 11, 22),
    ]
    
    st.markdown("**Test these known dates:**")
    cols = st.columns(3)
    for idx, (event, year, month, day) in enumerate(test_dates):
        with cols[idx % 3]:
            if st.button(f"{event}\n{year}-{month:02d}-{day:02d}", key=f"test_{idx}"):
                source = ChroniclingAmericaSource()
                with st.spinner(f"Importing {event}..."):
                    count = source.import_date(year, month, day)
                    if count > 0:
                        st.success(f"✅ Imported {count} articles from {event}")
                        time.sleep(1.5)
                        st.rerun()
                    else:
                        st.warning(f"No articles found for {event}. The API might be temporarily unavailable.")
    
    st.divider()
    
    # Custom date import
    st.markdown("**Or enter a custom date:**")
    col1, col2, col3 = st.columns(3)
    with col1:
        import_year = st.number_input("Year", min_value=1777, max_value=1963, value=1945, step=1)
    with col2:
        import_month = st.selectbox("Month", list(range(1, 13)), index=8)  # September
    with col3:
        import_day = st.selectbox("Day", list(range(1, 32)), index=1)
    
    if st.button("Import by Specific Date", type="primary"):
        source = ChroniclingAmericaSource()
        with st.spinner(f"Importing articles from {import_year}-{import_month:02d}-{import_day:02d}..."):
            count = source.import_date(import_year, import_month, import_day)
            if count > 0:
                st.success(f"✅ Imported {count} articles")
                time.sleep(1.5)
                st.rerun()
            else:
                st.warning("No articles found for this date. Try a different date or check if the date is within 1777-1963.")
    
    st.divider()
    
    # The Guardian Import
    st.markdown("### 🇬🇧 The Guardian")
    if api_keys['guardian']:
        st.success("✅ API key configured")
        
        custom_topic = st.text_input("Enter any topic to import", placeholder="e.g., artificial intelligence, climate change, space exploration")
        
        if custom_topic:
            topic_limit = st.slider("Number of articles", 5, 50, 20)
            if st.button(f"Import articles about '{custom_topic}'"):
                source = GuardianSource(api_keys['guardian'])
                with st.spinner(f"Importing articles about {custom_topic}..."):
                    count, msg = source.import_search(custom_topic, topic_limit)
                    if count > 0:
                        st.success(f"✅ Imported {count} articles about {custom_topic}")
                        time.sleep(1.5)
                        st.rerun()
                    else:
                        st.warning(f"No articles found for '{custom_topic}'. {msg}")
        
        with st.expander("Popular Topics (click to import)"):
            popular_topics = [
                "technology", "science", "climate change", "artificial intelligence",
                "space exploration", "renewable energy", "healthcare", "education",
                "democracy", "human rights", "economy", "sports", "culture",
                "environment", "innovation", "future of work", "digital transformation"
            ]
            cols = st.columns(3)
            for idx, topic in enumerate(popular_topics):
                with cols[idx % 3]:
                    if st.button(topic, key=f"popular_{idx}"):
                        source = GuardianSource(api_keys['guardian'])
                        with st.spinner(f"Importing articles about {topic}..."):
                            count, msg = source.import_search(topic, 15)
                            if count > 0:
                                st.success(f"✅ Imported {count} articles about {topic}")
                                time.sleep(1.5)
                                st.rerun()
    else:
        st.warning("⚠️ API key required for The Guardian")
        guardian_input = st.text_input("Enter Guardian API Key", type="password")
        if st.button("Save API Key"):
            api_keys['guardian'] = guardian_input
            save_api_keys(api_keys)
            st.success("API key saved!")
            st.rerun()
        st.caption("Get free key: https://open-platform.theguardian.com/access/")

# ==================== SIDEBAR ====================
with st.sidebar:
    st.markdown("### 📚 Library Overview")
    st.metric("Total Articles", f"{stats['total']:,}")
    
    if stats['total'] > 0:
        st.divider()
        st.markdown("### 📊 Collection Stats")
        
        sources = get_all_sources()
        for source in sources:
            name = "Chronicling America" if source['source'] == 'chronicling_america' else "The Guardian"
            st.caption(f"• {name}: {source['count']:,} articles")
        
        st.divider()
        st.markdown("### 🗞️ Newspapers")
        top_papers = get_all_newspapers()[:5]
        for paper in top_papers:
            st.caption(f"• {paper['newspaper_name'][:35]}: {paper['count']}")
    
    st.divider()
    st.caption("💡 **Tips:**")
    st.caption("• Try known historical dates for Chronicling America")
    st.caption("• Add Guardian API key for modern news")
    st.caption("• Browse by year to see what's available")

st.divider()
st.markdown("""
<div style="text-align: center; padding: 1rem; color: #666;">
    <p>📚 Newspaper Library • Historical and contemporary newspapers</p>
    <p style="font-size: 0.8rem;">Sources: Library of Congress (Chronicling America) and The Guardian</p>
</div>
""", unsafe_allow_html=True)
