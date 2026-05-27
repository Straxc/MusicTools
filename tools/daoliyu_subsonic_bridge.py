#!/usr/bin/env python3
"""
道理鱼 → Subsonic API 桥接服务

读取道理鱼的数据库，对外提供标准 Subsonic REST API
所有 Subsonic 客户端直接连接即可使用道理鱼音乐库

用法:
  python3 daoliyu_subsonic_bridge.py --host 0.0.0.0 --port 4040
  python3 daoliyu_subsonic_bridge.py --db /path/to/db.sqlite --media /path/to/music
  python3 daoliyu_subsonic_bridge.py --db postgres://user:pass@host:5433/db

默认会自动扫描 /vol1/@appshare/daoliyu.music/ 下的数据库
"""

import os, sys, json, time, random, argparse, sqlite3
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler

# 全局缓存
ARTIST_CACHE = {}
ALBUM_CACHE = {}
TRACK_CACHE = []
LAST_CACHE = 0
MEDIA_ROOT = ""
DB = None
DB_TYPE = "sqlite"

def find_db():
    """自动查找道理鱼数据库文件"""
    paths = [
        "/vol1/@appshare/daoliyu.music/data/daoliyu.sqlite",
        "/vol1/@appshare/daoliyu.music/db.sqlite3",
        "/vol1/@appshare/daoliyu.music/daoliyu.db",
    ]
    # 更通用的：扫描目录下的 .sqlite / .db 文件
    import glob
    for pat in ["/vol1/@appshare/daoliyu.music/**/*.sqlite*",
                "/vol1/@appshare/daoliyu.music/**/*.db"]:
        for f in glob.glob(pat, recursive=True):
            if os.path.getsize(f) > 10000:
                paths.append(f)
    for p in paths:
        if os.path.exists(p) and os.path.getsize(p) > 10000:
            return p
    return None

def list_tables(cur):
    """列出所有表名"""
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [r[0] for r in cur.fetchall()]

def find_column(cur, table, candidates):
    """在表中查找第一个存在的列名"""
    cur.execute(f"PRAGMA table_info('{table}')")
    cols = {r[1].lower() for r in cur.fetchall()}
    for c in candidates:
        if c.lower() in cols:
            return c
    return None

def init_db(path):
    """初始化连接并检测表结构"""
    global DB, DB_TYPE
    DB = sqlite3.connect(path)
    DB.row_factory = sqlite3.Row
    cur = DB.cursor()
    tables = list_tables(cur)
    print(f"数据库: {path} ({os.path.getsize(path)/1024/1024:.0f}MB)")
    print(f"表列表: {tables}")
    # 检测结构
    for t in ['Track', 'track', 'tracks']:
        if t in tables:
            cols = list_tables
            cur.execute(f"SELECT * FROM \"{t}\" LIMIT 1")
            print(f"  {t} 列: {[d[0] for d in cur.description]}")
            break
    return True

def refresh_cache():
    global LAST_CACHE, ARTIST_CACHE, ALBUM_CACHE, TRACK_CACHE
    now = time.time()
    if now - LAST_CACHE < 300 and ARTIST_CACHE:
        return
    cur = DB.cursor()

    # 检测表名
    tables = {t.lower() for t in list_tables(DB.cursor())}
    art_table = next((t for t in ['Artist', 'artist', 'artists'] if t in tables or t.lower() in tables), None)
    alb_table = next((t for t in ['Album', 'album', 'albums'] if t in tables or t.lower() in tables), None)
    trk_table = next((t for t in ['Track', 'track', 'tracks'] if t in tables or t.lower() in tables), None)

    if not all([art_table, alb_table, trk_table]):
        print("警告: 未找到 Artist/Album/Track 表")
        return

    # 检测列名 (道理鱼可能用 CamelCase 或 snake_case)
    def detect_cols(table):
        cur.execute(f"PRAGMA table_info('{table}')")
        return {r[1].lower(): r[1] for r in cur.fetchall()}

    art_cols = detect_cols(art_table)
    alb_cols = detect_cols(alb_table)
    trk_cols = detect_cols(trk_table)

    # 艺人
    id_c = art_cols.get('id', 'id')
    name_c = art_cols.get('name', 'name')
    cur.execute(f'SELECT "{id_c}", "{name_c}" FROM "{art_table}"')
    ARTIST_CACHE = {str(r[0]): r[1] for r in cur.fetchall()}

    # 专辑
    ALBUM_CACHE.clear()
    aid_c = next((c for c in ['artistId', 'artist_id', 'artistid'] if c.lower() in alb_cols), 'artistId')
    yr_c = next((c for c in ['year', 'releaseYear'] if c.lower() in alb_cols), None)
    al_id_c = alb_cols.get('id', 'id')
    al_name_c = alb_cols.get('name', 'name')
    cur.execute(f'SELECT "{al_id_c}", "{al_name_c}", "{aid_c}", "{yr_c}" FROM "{alb_table}"' if yr_c else
                f'SELECT "{al_id_c}", "{al_name_c}", "{aid_c}" FROM "{alb_table}"')
    for r in cur.fetchall():
        ALBUM_CACHE[str(r[0])] = {
            'name': r[1], 'artist_id': str(r[2]),
            'year': str(r[3]) if yr_c and len(r) > 3 else ''
        }

    # 歌曲
    TRACK_CACHE.clear()
    tk_id_c = trk_cols.get('id', 'id')
    tk_title_c = trk_cols.get('title', 'name')
    album_id_c = next((c for c in ['albumId', 'album_id'] if c.lower() in trk_cols), 'albumId')
    artist_id_c = next((c for c in ['artistId', 'artist_id'] if c.lower() in trk_cols), 'artistId')
    dur_c = next((c for c in ['duration', 'length'] if c.lower() in trk_cols), None)
    tn_c = next((c for c in ['trackNumber', 'track', 'track_number'] if c.lower() in trk_cols), None)
    fp_c = next((c for c in ['filePath', 'path', 'file_path'] if c.lower() in trk_cols), None)

    fields = [f'"{tk_id_c}"', f'"{tk_title_c}"', f'"{album_id_c}"', f'"{artist_id_c}"']
    if dur_c: fields.append(f'"{dur_c}"')
    if tn_c: fields.append(f'"{tn_c}"')
    if fp_c: fields.append(f'"{fp_c}"')

    cur.execute(f'SELECT {",".join(fields)} FROM "{trk_table}"')
    TRACK_CACHE = [dict(r) for r in cur.fetchall()]
    LAST_CACHE = now
    print(f"  已加载: {len(ARTIST_CACHE)} 艺人, {len(ALBUM_CACHE)} 专辑, {len(TRACK_CACHE)} 歌曲")

def find_media_root():
    """自动查找音乐目录"""
    # 扫描道理鱼配置或常见路径
    paths = [
        "/vol1/1000/music",
        "/vol1/1000/Music",
        "/vol1/media",
        "/vol1/1000/media",
    ]
    # 也扫描 daoliyu 目录下的 media 映射
    import glob
    for f in glob.glob("/vol1/@appshare/daoliyu.music/**/media", recursive=True):
        if os.path.isdir(f):
            paths.insert(0, f)
    for p in paths:
        if os.path.isdir(p) and len(os.listdir(p)) > 0:
            return p
    return "/vol1/1000/music"

class SubsonicHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if '/rest/stream' in path or '/rest/stream/' in path:
            self.serve_stream(qs)
            return

        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        resp = {"subsonic-response": {"status": "ok", "version": "1.16.0",
                "xmlns": "http://subsonic.org/restapi"}}

        try:
            refresh_cache()
            if '/rest/getArtists' in path:
                self.handle_get_artists(resp)
            elif '/rest/getArtist' in path:
                self.handle_get_artist(resp, qs)
            elif '/rest/getAlbumList2' in path:
                self.handle_get_album_list(resp, qs)
            elif '/rest/getAlbum' in path:
                self.handle_get_album(resp, qs)
            elif '/rest/search3' in path or '/rest/search2' in path:
                self.handle_search(resp, qs)
            elif '/rest/getRandomSongs' in path:
                self.handle_random_songs(resp, qs)
            elif '/rest/ping' in path or '/rest/ping.view' in path:
                pass
            else:
                resp['subsonic-response']['error'] = {'code': 0, 'message': 'Not implemented'}
        except Exception as e:
            resp['subsonic-response']['status'] = 'failed'
            resp['subsonic-response']['error'] = {'code': 1, 'message': str(e)}

        self.wfile.write(json.dumps(resp, ensure_ascii=False, default=str).encode())

    def handle_get_artists(self, resp):
        items = []
        for aid, name in ARTIST_CACHE.items():
            cnt = sum(1 for a in ALBUM_CACHE.values() if a['artist_id'] == aid)
            items.append({"id": f"ar-{aid}", "name": name, "albumCount": cnt})
        items.sort(key=lambda x: x['name'])
        idx = {}
        for item in items:
            k = item['name'][0].upper() if item['name'] else '#'
            idx.setdefault(k, []).append(item)
        resp['subsonic-response']['artists'] = {
            "index": [{"name": k, "artist": v} for k, v in sorted(idx.items())]
        }

    def handle_get_artist(self, resp, qs):
        aid = (qs.get('id', [''])[0]).replace('ar-', '')
        name = ARTIST_CACHE.get(aid, 'Unknown')
        albums = []
        for al_id, al in ALBUM_CACHE.items():
            if al['artist_id'] == aid:
                albums.append({"id": f"al-{al_id}", "name": al['name'],
                               "artist": name, "year": al['year'],
                               "artistId": f"ar-{aid}"})
        resp['subsonic-response']['artist'] = {"id": f"ar-{aid}", "name": name, "album": albums}

    def handle_get_album_list(self, resp, qs):
        size = int(qs.get('size', ['50'])[0])
        offset = int(qs.get('offset', ['0'])[0])
        atype = qs.get('type', ['newest'])[0]
        items = []
        for al_id, al in ALBUM_CACHE.items():
            items.append({"id": f"al-{al_id}", "name": al['name'],
                          "artist": ARTIST_CACHE.get(al['artist_id'], ''),
                          "year": al['year'], "artistId": f"ar-{al['artist_id']}"})
        if atype in ('newest', 'byYear'):
            items.sort(key=lambda a: a['year'], reverse=True)
        else:
            items.sort(key=lambda a: a['name'])
        resp['subsonic-response']['albumList2'] = {"album": items[offset:offset+size]}

    def handle_get_album(self, resp, qs):
        al_id = (qs.get('id', [''])[0]).replace('al-', '')
        al = ALBUM_CACHE.get(al_id, {})
        aname = ARTIST_CACHE.get(al.get('artist_id'), '')
        songs = []
        for t in TRACK_CACHE:
            tk = dict(t)
            if str(tk.get('albumId', tk.get('album_id', ''))) == al_id:
                tid = str(tk.get('id', ''))
                songs.append({"id": f"trk-{tid}", "title": tk.get('title', tk.get('name', '')),
                              "artist": aname, "album": al.get('name', ''),
                              "duration": tk.get('duration', tk.get('length', 0)) or 0,
                              "track": tk.get('trackNumber', tk.get('track', 0)) or 0,
                              "albumId": f"al-{al_id}", "artistId": f"ar-{al.get('artist_id','')}"})
        songs.sort(key=lambda s: s['track'])
        resp['subsonic-response']['album'] = {"id": f"al-{al_id}", "name": al.get('name', ''),
                                               "artist": aname, "song": songs}

    def handle_search(self, resp, qs):
        query = qs.get('query', [''])[0].lower()
        results = []
        for t in TRACK_CACHE:
            tk = dict(t)
            title = (tk.get('title', tk.get('name', '')) or '').lower()
            if query in title:
                al = ALBUM_CACHE.get(str(tk.get('albumId', tk.get('album_id', ''))), {})
                results.append({"id": f"trk-{str(tk.get('id',''))}", "title": tk.get('title', tk.get('name', '')),
                                "artist": ARTIST_CACHE.get(str(tk.get('artistId', tk.get('artist_id', ''))), ''),
                                "album": al.get('name', ''), "duration": tk.get('duration', tk.get('length', 0)) or 0})
        resp['subsonic-response']['searchResult3'] = {"song": results[:50]}

    def handle_random_songs(self, resp, qs):
        size = int(qs.get('size', ['10'])[0])
        if not TRACK_CACHE: return
        sample = random.sample(TRACK_CACHE, min(size, len(TRACK_CACHE)))
        songs = []
        for t in sample:
            tk = dict(t)
            al = ALBUM_CACHE.get(str(tk.get('albumId', tk.get('album_id', ''))), {})
            songs.append({"id": f"trk-{str(tk.get('id',''))}", "title": tk.get('title', tk.get('name', '')),
                          "artist": ARTIST_CACHE.get(str(tk.get('artistId', tk.get('artist_id', ''))), ''),
                          "album": al.get('name', ''), "duration": tk.get('duration', tk.get('length', 0)) or 0})
        resp['subsonic-response']['randomSongs'] = {"song": songs}

    def serve_stream(self, qs):
        track_id = (qs.get('id', [''])[0]).replace('trk-', '')
        # 从缓存找文件路径
        for t in TRACK_CACHE:
            tk = dict(t)
            if str(tk.get('id', '')) == track_id:
                fp = tk.get('filePath', tk.get('path', tk.get('file_path', '')))
                if fp:
                    full = os.path.join(MEDIA_ROOT, fp.lstrip('/')) if not os.path.isabs(fp) else fp
                    if os.path.exists(full):
                        self._send_file(full)
                        return
        # 如果没路径，直接目录搜索
        for root, _, files in os.walk(MEDIA_ROOT):
            for f in files:
                if track_id in f:
                    self._send_file(os.path.join(root, f))
                    return
        self.send_error(404, "File not found")

    def _send_file(self, fp):
        self.send_response(200)
        ext = os.path.splitext(fp)[1].lower()
        mimes = {'.flac': 'audio/flac', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.ogg': 'audio/ogg'}
        self.send_header('Content-Type', mimes.get(ext, 'audio/mpeg'))
        self.send_header('Content-Length', str(os.path.getsize(fp)))
        self.end_headers()
        with open(fp, 'rb') as f:
            self.wfile.write(f.read())

    def log_message(self, format, *args):
        pass


def main():
    global MEDIA_ROOT
    parser = argparse.ArgumentParser(description='道理鱼 Subsonic 桥')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=4040)
    parser.add_argument('--db', default=None, help='数据库路径 (自动检测)')
    parser.add_argument('--media', default=None, help='音乐目录 (自动检测)')
    args = parser.parse_args()

    # 找数据库
    db_path = args.db or os.environ.get("DLY_DB") or find_db()
    if not db_path:
        print("错误: 未找到数据库。手动指定: --db /path/to/db.sqlite")
        sys.exit(1)
    print(f"数据库: {db_path}")
    init_db(db_path)

    # 找音乐目录
    MEDIA_ROOT = args.media or os.environ.get("MEDIA_PATH") or find_media_root()
    print(f"音乐目录: {MEDIA_ROOT}")

    refresh_cache()

    server = HTTPServer((args.host, args.port), SubsonicHandler)
    print(f"→ 桥已启动: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

if __name__ == '__main__':
    main()
