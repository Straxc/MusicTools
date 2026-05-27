"""
MusicTools v1.1 — 音乐文件工具箱
  双击运行: 交互菜单
  命令行:   musictools tag <dir>
  图形界面: musictools --gui
"""
import os, sys, re, struct, json, time, math, shutil, threading, subprocess
from urllib.request import Request, urlopen
from urllib.parse import quote

try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except: pass

VERSION = "1.2.0"
_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

_NETEASE_SEARCH = 'https://music.163.com/api/search/get'
_NETEASE_LYRIC  = 'https://music.163.com/api/song/lyric'
_QQ_SEARCH      = 'https://c.y.qq.com/soso/fcgi-bin/client_search_cp'
_QQ_LYRIC       = 'https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg'


# ═══════════════════════════════════════════
#  基础工具
# ═══════════════════════════════════════════

def safe_print(*a, end='\n'):
    try: print(*a, end=end)
    except UnicodeEncodeError:
        s = " ".join(str(x) for x in a)
        print(s.encode('ascii', errors='replace').decode('ascii'), end=end)


def eprint(msg):
    """GUI 输出回调"""
    print(msg, flush=True)


def safe_exit():
    """安全的退出等待（命令行模式下暂停，管道模式下直接退出）"""
    try:
        if sys.stdin.isatty():
            input("\n按回车退出...")
    except (EOFError, OSError, AttributeError):
        pass
    sys.exit(0)


# ═══════════════════════════════════════════
#  标签读取 (FLAC/MP3)
# ═══════════════════════════════════════════

def read_tags(fp):
    ext = os.path.splitext(fp)[1].lower()
    tags = {}
    if ext == '.flac':
        with open(fp, 'rb') as f: data = bytearray(f.read())
        pos = 4
        while pos < len(data):
            h = data[pos:pos+4]; bt, last = h[0] & 0x7F, h[0] & 0x80
            bl = struct.unpack('>I', b'\x00' + h[1:4])[0]
            if bt == 4:
                vc = data[pos+4:pos+4+bl]; vl = struct.unpack('<I', vc[:4])[0]
                nt = struct.unpack('<I', vc[4+vl:8+vl])[0]; off = 8+vl
                for _ in range(nt):
                    tl = struct.unpack('<I', vc[off:off+4])[0]
                    t = vc[off+4:off+4+tl].decode('utf-8', errors='replace')
                    if '=' in t: k, v = t.split('=', 1); tags[k.upper()] = v
                    off += 4+tl
                break
            pos += 4+bl
            if last: break
    elif ext == '.mp3':
        try:
            import mutagen.mp3
            mp3 = mutagen.mp3.MP3(fp)
            if mp3.tags:
                for fid in ['TIT2', 'TPE1', 'TALB', 'TRCK', 'TPOS']:
                    f = mp3.tags.get(fid)
                    if f: tags[fid] = str(f)
        except Exception: pass
    return tags


def has_lyrics(fp):
    t = read_tags(fp)
    for k in t:
        if k in ('LYRICS', 'UNSYNCEDLYRICS', 'USLT'): return True
    return os.path.exists(os.path.splitext(fp)[0] + '.lrc')


# ═══════════════════════════════════════════
#  文件名解析
# ═══════════════════════════════════════════

def parse_filename(fn):
    name = fn
    for ext in ('.flac', '.mp3', '.wav', '.lrc'):
        if name.lower().endswith(ext): name = name[:-len(ext)]; break
    r = {'track': None, 'artist': None, 'title': name.strip()}
    m = re.match(r'^(\d{1,3})\s*[.\、_\-\s]+(.+?)\s*\-\s*(.+)$', name.strip())
    if m:
        r['track'] = int(m.group(1)); r['artist'] = m.group(2).strip()
        r['title'] = m.group(3).strip()
        return r
    m = re.match(r'^(\d{1,3})\s*[.\、_\-\s]+(.+)$', name.strip())
    if m:
        r['track'] = int(m.group(1)); r['title'] = m.group(2).strip()
    return r


# ═══════════════════════════════════════════
#  FLAC 标签写入
# ═══════════════════════════════════════════

def write_flac_tags(fp, updates):
    with open(fp, 'rb') as f: data = bytearray(f.read())
    if data[:4] != b'fLaC': return False
    blocks = []; pos = 4; audio_start = 0
    while pos < len(data):
        h = data[pos:pos+4]; bt, last = h[0] & 0x7F, h[0] & 0x80
        bl = struct.unpack('>I', b'\x00' + h[1:4])[0]
        blocks.append({'type': bt, 'offset': pos, 'length': bl, 'is_last': bool(last)})
        pos += 4+bl
        if last: audio_start = pos; break
    vc_idx = next((i for i, b in enumerate(blocks) if b['type'] == 4), None)
    if vc_idx is not None:
        vc = data[blocks[vc_idx]['offset']+4:blocks[vc_idx]['offset']+4+blocks[vc_idx]['length']]
        vl = struct.unpack('<I', vc[:4])[0]; vendor = vc[4:4+vl]
        nt = struct.unpack('<I', vc[4+vl:8+vl])[0]; off = 8+vl
        existing = {}
        for _ in range(nt):
            tl = struct.unpack('<I', vc[off:off+4])[0]
            t = vc[off+4:off+4+tl].decode('utf-8', errors='replace')
            if '=' in t: k, v = t.split('=', 1); existing[k.upper()] = v
            off += 4+tl
        for k, v in updates.items(): existing[k] = v
    else:
        vendor = b'reference libFLAC 1.4.3 20230623'; existing = updates
        blocks.insert(1, {'type': 4, 'is_last': False, 'offset': -1, 'length': 0}); vc_idx = 1
    tags = [f"{k}={v}" for k, v in existing.items()]
    new_vc = bytearray()
    new_vc += struct.pack('<I', len(vendor)); new_vc += vendor
    new_vc += struct.pack('<I', len(tags))
    for t in tags:
        tb = t.encode('utf-8'); new_vc += struct.pack('<I', len(tb)); new_vc += tb
    result = bytearray(b'fLaC'); n = len(blocks)
    for i, b in enumerate(blocks):
        bt = b['type'] | (0x80 if i == n-1 else 0)
        if i == vc_idx:
            result.append(bt); result += struct.pack('>I', len(new_vc))[1:4]; result += new_vc
        else:
            bd = data[b['offset']+4:b['offset']+4+b['length']]
            result.append(bt); result += struct.pack('>I', b['length'])[1:4]; result += bd
    result += data[audio_start:]
    with open(fp, 'wb') as f: f.write(result)
    return True


def write_mp3_tags(fp, updates):
    import mutagen.id3, mutagen.mp3
    mp3 = mutagen.mp3.MP3(fp)
    if mp3.tags is None: mp3.tags = mutagen.id3.ID3()
    m = {'TITLE': ('TIT2', mutagen.id3.TIT2), 'ARTIST': ('TPE1', mutagen.id3.TPE1),
         'ALBUM': ('TALB', mutagen.id3.TALB), 'TRACKNUMBER': ('TRCK', mutagen.id3.TRCK),
         'DISCNUMBER': ('TPOS', mutagen.id3.TPOS), 'DATE': ('TDRC', mutagen.id3.TDRC)}
    wrote = False
    for k, v in updates.items():
        if k not in m: continue
        fid, cls = m[k]
        if mp3.tags.get(fid) and str(mp3.tags[fid]).strip(): continue
        mp3.tags.add(cls(encoding=3, text=v)); wrote = True
    if wrote: mp3.save()
    return True


def write_tags(fp, updates):
    ext = os.path.splitext(fp)[1].lower()
    if ext == '.flac': return write_flac_tags(fp, updates)
    if ext == '.mp3': return write_mp3_tags(fp, updates)
    return False


# ═══════════════════════════════════════════
#  歌词 API (原生接口: 网易云 + QQ音乐)
# ═══════════════════════════════════════════

def _api_get(url, referer, timeout=15):
    """通用 JSON API 请求"""
    try:
        req = Request(url, headers={'User-Agent': _UA, 'Referer': referer})
        resp = urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


def search_netease(title, artist="", n=4):
    q = f"{artist} {title}".strip() if artist else title
    url = f"{_NETEASE_SEARCH}?s={quote(q)}&type=1&limit={n}"
    data = _api_get(url, 'https://music.163.com/')
    if not data or data.get('code') != 200: return []
    songs = data.get('result', {}).get('songs', [])
    if not isinstance(songs, list): return []
    results = []
    for s in songs:
        sid = s.get('id')
        if not sid: continue
        artists = s.get('artists') or s.get('ar') or []
        ar_name = artists[0].get('name', '') if artists else ''
        album = s.get('album') or {}
        al_name = album.get('name', '') if isinstance(album, dict) else ''
        results.append({'src': 'netease', 'id': sid, 'name': s.get('name', ''),
                        'artist': ar_name, 'album': al_name})
    return results


def search_qq(title, artist="", n=4):
    q = f"{artist} {title}".strip() if artist else title
    url = f"{_QQ_SEARCH}?p=1&n={n}&w={quote(q)}&format=json"
    data = _api_get(url, 'https://y.qq.com/')
    if not data or data.get('code') != 0: return []
    songs = data.get('data', {}).get('song', {}).get('list', [])
    if not isinstance(songs, list): return []
    results = []
    for s in songs:
        mid = s.get('songmid', '') or s.get('mid', '')
        if not mid: continue
        singers = s.get('singer') or []
        ar_name = singers[0].get('name', '') if singers else ''
        results.append({'src': 'qq', 'id': mid, 'mid': mid,
                        'name': s.get('songname', s.get('name', '')),
                        'artist': ar_name, 'album': s.get('albumname', s.get('album', ''))})
    return results


def fetch_lyric_netease(sid):
    data = _api_get(f"{_NETEASE_LYRIC}?id={sid}&lv=-1", 'https://music.163.com/')
    if not data or data.get('code') != 200: return None
    lrc_obj = data.get('lrc') or {}
    return (lrc_obj.get('lyric') if isinstance(lrc_obj, dict) else None)


def fetch_lyric_qq(mid):
    url = f"{_QQ_LYRIC}?songmid={mid}&format=json&nobase64=1"
    data = _api_get(url, 'https://y.qq.com/')
    if not data or data.get('code') != 0: return None
    lrc = data.get('lyric') or data.get('lyricStr') or ''
    if isinstance(lrc, str) and lrc.strip():
        return lrc
    return None


def match_score(qt, qa, sn, sa):
    qt = re.sub(r'[^\w\u4e00-\u9fff]', '', qt).lower()
    qa = re.sub(r'[^\w\u4e00-\u9fff]', '', qa).lower()
    sn = re.sub(r'[^\w\u4e00-\u9fff]', '', sn).lower()
    sa = re.sub(r'[^\w\u4e00-\u9fff]', '', sa).lower()
    if not qt: return 0
    if qt == sn: sn_s = 0.7
    elif qt in sn or sn in qt: sn_s = 0.55
    else: sn_s = 0.35 * sum(1 for c in qt if c in sn) / len(qt)
    if not qa or not sa: sa_s = 0.15
    elif qa == sa: sa_s = 0.3
    elif qa in sa or sa in qa: sa_s = 0.25
    else: sa_s = 0.15 * sum(1 for c in qa if c in sa) / max(len(qa), 1)
    return sn_s + sa_s


def save_lrc(dirpath, basename, lrc_text, word_lrc=False):
    p = os.path.join(dirpath, basename + '.lrc')
    with open(p, 'w', encoding='utf-8') as f: f.write(lrc_text)
    if word_lrc:
        sub = os.path.join(dirpath, '逐字歌词')
        os.makedirs(sub, exist_ok=True)
        shutil.copy2(p, os.path.join(sub, basename + '.lrc'))
    return p


# ═══════════════════════════════════════════
#  通用辅助: 文件扫描 / 元数据 / 搜索 / 保存
# ═══════════════════════════════════════════

def collect_audio_files(root_dir):
    """扫描目录, 返回所有 .flac/.mp3/.wav 文件路径 (跳过逐字歌词子目录)"""
    files = []
    try:
        for r, _, fs in os.walk(root_dir):
            if '逐字歌词' in r:
                continue
            for f in fs:
                if os.path.splitext(f)[1].lower() in ('.flac', '.mp3', '.wav'):
                    files.append(os.path.join(r, f))
    except Exception:
        pass
    return sorted(files)


def resolve_song_metadata(fp):
    """从标签或文件名获取 (title, artist)"""
    tags = read_tags(fp)
    title = tags.get('TITLE', '') or tags.get('TIT2', '')
    artist = tags.get('ARTIST', '') or tags.get('TPE1', '')
    if not title:
        fn = os.path.basename(fp)
        p = parse_filename(fn)
        title = p['title'] or fn
        artist = p.get('artist', '') or artist
    return title, artist


def search_both(title, artist, n=3):
    """联合搜索网易云 + QQ音乐, 去重评分排序, 返回 top 结果"""
    all_r = []
    seen = set()
    for r in search_netease(title, artist, n) + search_qq(title, artist, n):
        k = (r['name'], r['artist'])
        if k in seen: continue
        seen.add(k)
        r['score'] = match_score(title, artist, r['name'], r['artist'])
        all_r.append(r)
    all_r.sort(key=lambda x: x['score'], reverse=True)
    return all_r[:6]


def fetch_lyric_by_result(result):
    """根据搜索结果下载歌词"""
    if result['src'] == 'netease':
        return fetch_lyric_netease(result['id'])
    return fetch_lyric_qq(result.get('mid', result['id']))


def embed_lyrics_to_file(fp, lrc_text, word_lrc=False):
    """将歌词嵌入音频标签 + 保存 .lrc 文件 (word_lrc=True 时额外复制到 逐字歌词 子目录)"""
    ext = os.path.splitext(fp)[1].lower()
    if ext == '.flac':
        write_flac_tags(fp, {'LYRICS': lrc_text})
    elif ext == '.mp3':
        import mutagen.id3, mutagen.mp3
        mp3 = mutagen.mp3.MP3(fp)
        if mp3.tags is None:
            mp3.tags = mutagen.id3.ID3()
        mp3.tags.delall('USLT'); mp3.tags.delall('SYLT')
        mp3.tags.add(mutagen.id3.USLT(encoding=3, lang='chi', desc='', text=lrc_text))
        mp3.save()
    fn = os.path.splitext(os.path.basename(fp))[0]
    save_lrc(os.path.dirname(fp), fn, lrc_text, word_lrc)


def _is_kana_char(c):
    return '\u3040' <= c <= '\u30ff'


def _has_kana(s):
    return any(_is_kana_char(c) for c in s)


# 中文特有标记字 (现代日文中极少出现或以假名替代, 高效区分中文)
_CN_MARKERS = set('的了吗呢吧着过被把说之这那你他她与')


def _is_chinese_text(s):
    """检测文本是否包含中文特有的语法标记"""
    return any(c in s for c in _CN_MARKERS)


def _split_jp_prefix(text):
    """尝试将无假名段落拆出前缀日语词 (2 字 和文漢字)
    规则: 前 2 字无中文标记, 剩余含中文标记且 ≥3 字 → 拆分
    返回拆分索引, 无法拆分返回 0
    """
    if len(text) < 5:
        return 0
    prefix = text[:2]
    rest = text[2:]
    if _is_chinese_text(prefix):
        return 0
    if _is_chinese_text(rest) and len(rest) >= 3:
        return 2
    return 0


def _bilingual_split_parts(text):
    """将中日混排文本拆分为 (日语, 中文) 配对列表"""
    # ── 全角空格分段 ──
    if '　' in text:
        raw_parts = [p for p in text.split('　') if p]
        typed = []  # list of ('jp'|'cn', content)

        for part in raw_parts:
            pk = _has_kana(part)
            pc = _is_chinese_text(part)

            if pk:
                # 含假名 → 日语; 检查末尾有无中文尾巴
                last_k = -1
                for i, c in enumerate(part):
                    if _is_kana_char(c):
                        last_k = i
                if last_k >= 0 and last_k + 1 < len(part):
                    after = part[last_k + 1:]
                    # 尾巴: 无假名, 且有中文标记或 >=4 字 → 视为中文
                    if not _has_kana(after) and (_is_chinese_text(after) or len(after) >= 4):
                        typed.append(('jp', part[:last_k + 1]))
                        typed.append(('cn', after))
                    else:
                        typed.append(('jp', part))
                else:
                    typed.append(('jp', part))

            elif pc:
                # 无假名, 有中文标记 → 尝试拆出前缀日语词
                split_at = _split_jp_prefix(part)
                if split_at:
                    typed.append(('jp', part[:split_at]))
                    typed.append(('cn', part[split_at:]))
                else:
                    typed.append(('cn', part))

            else:
                # 无假名且无中文标记 → 短则日语, 长则中文
                if len(part) <= 3:
                    typed.append(('jp', part))
                else:
                    typed.append(('cn', part))

        # ── 收集并配对 ──
        jp_items = [c for t, c in typed if t == 'jp']
        cn_items = [c for t, c in typed if t == 'cn']

        paired = []
        for i in range(max(len(jp_items), len(cn_items))):
            jp = jp_items[i] if i < len(jp_items) else ''
            cn = cn_items[i] if i < len(cn_items) else ''
            if jp and cn:
                paired.append(f"{jp} {cn}")
            elif jp:
                # 多余日语插入前一行的中文前
                if paired and ' ' in paired[-1]:
                    parts = paired[-1].split(' ', 1)
                    paired[-1] = f"{parts[0]} {jp} {parts[1]}"
                else:
                    paired.append(jp)
            elif cn:
                # 多余中文追加到前一行
                if paired:
                    paired[-1] = f"{paired[-1]} {cn}"
                else:
                    paired.append(cn)
        return paired

    # ── 无全角空格: 搜索假名边界 ──
    last_kana = -1
    for i, c in enumerate(text):
        if _is_kana_char(c):
            last_kana = i
    if last_kana >= 0 and last_kana + 1 < len(text):
        jp = text[:last_kana + 1]
        cn = text[last_kana + 1:]
        if jp and cn:
            return [f"{jp} {cn}"]
    return [text]


def preprocess_lyrics_text(text, bilingual=False):
    """预处理歌词文本为 LRCMaker 友好格式
    - 全角空格(　)处拆分为独立行
    - 保留英文/拉丁字母句中的空格不拆分
    - 保留空行作为段落分隔
    - bilingual=True 时: 将日/中混排文本拆分并配对为"日语 中文"每行
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append('')
            continue

        # 检测是否为英文/拉丁为主的行 (保留空格)
        alpha = sum(1 for c in stripped if c.isascii() and c.isalpha())
        total = max(len(stripped.replace(' ', '').replace('　', '')), 1)
        if alpha / total > 0.5:
            result.append(stripped)
            continue

        # 双语模式: 拆解日/中混合文本
        if bilingual:
            paired = _bilingual_split_parts(stripped)
            result.extend(paired)
            continue

        # 通用模式: 全角空格处拆行为独立行
        if '　' in stripped:
            parts = [p.strip() for p in stripped.split('　') if p.strip()]
            result.extend(parts)
        else:
            result.append(stripped)
    return '\n'.join(result)

def cmd_tag(root_dir, log=eprint):
    log(f"  tag: 文件名 -> 标签写入")
    log(f"  目录: {root_dir}\n")
    tagged = skipped = errors = 0
    for dirpath, _, files in os.walk(root_dir):
        if '逐字歌词' in dirpath: continue
        album = re.sub(r'^[\d\[\].\- \s、☆★♯♭#]+', '', os.path.basename(dirpath)).strip()
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in ('.flac', '.mp3'): continue
            fp = os.path.join(dirpath, fn)
            existing = read_tags(fp)
            parsed = parse_filename(fn)
            ups = {}
            if parsed['track'] and 'TRACKNUMBER' not in existing and 'TRCK' not in existing:
                ups['TRACKNUMBER'] = str(parsed['track'])
            if parsed['title'] and 'TITLE' not in existing and 'TIT2' not in existing:
                ups['TITLE'] = parsed['title']
            if album and 'ALBUM' not in existing and 'TALB' not in existing:
                ups['ALBUM'] = album
            if parsed.get('artist') and 'ARTIST' not in existing and 'TPE1' not in existing:
                ups['ARTIST'] = parsed['artist']
            if not ups: skipped += 1; continue
            try:
                if write_tags(fp, ups):
                    tagged += 1
                    if tagged <= 30:
                        log(f"  [{os.path.basename(dirpath)}] {fn[:40]} <- {ups}")
            except Exception as e:
                errors += 1
                log(f"  [ERR] {fn[:40]}: {e}")
    log(f"\n  写入:{tagged}  跳过:{skipped}  错误:{errors}")


# ═══════════════════════════════════════════
#  命令: fix
# ═══════════════════════════════════════════

def cmd_fix(root_dir, log=eprint):
    log(f"  fix: PICTURE 封面元数据修复")
    log(f"  目录: {root_dir}\n")

    def fix_pic(pd):
        pt = struct.unpack('>I', pd[:4])[0]; ml = struct.unpack('>I', pd[4:8])[0]
        m = pd[8:8+ml] if ml>0 else b""; do = 8+ml; dl = struct.unpack('>I', pd[do:do+4])[0]
        io = do+4+dl
        w = struct.unpack('>I', pd[io:io+4])[0]; h = struct.unpack('>I', pd[io+4:io+8])[0]
        cd = struct.unpack('>I', pd[io+8:io+12])[0]; nc = struct.unpack('>I', pd[io+12:io+16])[0]
        ps = struct.unpack('>I', pd[io+16:io+20])[0]; img = pd[io+20:io+20+ps]
        if ml>0 and w>0 and h>0: return pd
        if ml==0:
            if img[:2]==b'\xff\xd8': m=b'image/jpeg'
            elif img[:4]==b'\x89PNG': m=b'image/png'
            else: m=b'image/jpeg'
        if w==0 or h==0: w=h=1000
        buf = bytearray(); buf += struct.pack('>I', pt); buf += struct.pack('>I', len(m)); buf += m
        buf += struct.pack('>I', dl); buf += pd[do+4:do+4+dl]
        buf += struct.pack('>I', w); buf += struct.pack('>I', h)
        buf += struct.pack('>I', cd); buf += struct.pack('>I', nc); buf += struct.pack('>I', ps); buf += img
        return bytes(buf)

    fixed = 0
    for dirpath, _, files in os.walk(root_dir):
        if '逐字歌词' in dirpath: continue
        for fn in sorted(files):
            if not fn.lower().endswith('.flac'): continue
            fp = os.path.join(dirpath, fn)
            with open(fp, 'rb') as f: data = bytearray(f.read())
            if data[:4] != b'fLaC': continue
            blocks = []; pos = 4; audio_start = 0; pic_idx = None
            while pos < len(data):
                h = data[pos:pos+4]; bt, last = h[0] & 0x7F, h[0] & 0x80
                bl = struct.unpack('>I', b'\x00' + h[1:4])[0]
                if bt == 6: pic_idx = len(blocks)
                blocks.append({'type': bt, 'offset': pos, 'length': bl, 'is_last': bool(last)})
                pos += 4+bl
                if last: audio_start = pos; break
            if pic_idx is None: continue
            b = blocks[pic_idx]
            old = bytes(data[b['offset']+4:b['offset']+4+b['length']])
            new = fix_pic(old)
            if old == new: continue
            b['length'] = len(new); b['_data'] = new
            result = bytearray(b'fLaC')
            for i, blk in enumerate(blocks):
                bt = blk['type'] | (0x80 if i==len(blocks)-1 else 0)
                d = blk.pop('_data', data[blk['offset']+4:blk['offset']+4+blk['length']])
                result.append(bt); result += struct.pack('>I', blk['length'])[1:4]; result += d
            result += data[audio_start:]
            with open(fp, 'wb') as f: f.write(result)
            fixed += 1
            if fixed <= 30: log(f"  [FIXED] {fn[:50]}")
    log(f"\n  修复: {fixed} 个")


# ═══════════════════════════════════════════
#  命令: check
# ═══════════════════════════════════════════

def cmd_check(root_dir, log=eprint):
    log(f"  check: 文件检测")
    log(f"  目录: {root_dir}\n")
    total = 0; no_title = 0; no_artist = 0; no_lrc = 0
    bad_title = []; bad_artist = []; bad_lrc = []
    all_files = []
    limit = 20
    for dirpath, _, files in os.walk(root_dir):
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in ('.flac', '.mp3', '.wav'): continue
            total += 1; fp = os.path.join(dirpath, fn)
            tags = read_tags(fp)
            has_t = bool(tags.get('TITLE') or tags.get('TIT2'))
            has_a = bool(tags.get('ARTIST') or tags.get('TPE1'))
            has_l = has_lyrics(fp)
            all_files.append((fp, not has_t, not has_a, not has_l))
            if not has_t: no_title += 1
            if len(bad_title) < limit and not has_t: bad_title.append(fp)
            if not has_a: no_artist += 1
            if len(bad_artist) < limit and not has_a: bad_artist.append(fp)
            if not has_l: no_lrc += 1
            if len(bad_lrc) < limit and not has_l: bad_lrc.append(fp)
    log(f"  总计: {total} 个文件")
    log(f"  缺标题: {no_title}  缺艺术家: {no_artist}  缺歌词: {no_lrc}")
    for count, lst, label in [(no_title, bad_title, "缺标题"),
                               (no_artist, bad_artist, "缺艺术家"),
                               (no_lrc, bad_lrc, "缺歌词")]:
        if count:
            more = f" ... 等 {count} 个" if count > len(lst) else ""
            log(f"   ─ {label}: {', '.join(os.path.basename(f) for f in lst[:10])}{more}")

    # 写出完整报告到 exe 同目录
    report_fn = f"检测报告_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    report_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv[0] else os.getcwd()
    report_path = os.path.join(report_dir, report_fn)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"文件检测报告 - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"目录: {root_dir}\n\n")
        f.write(f"共 {total} 个文件\n")
        f.write(f"缺标题: {no_title}\n")
        f.write(f"缺艺术家: {no_artist}\n")
        f.write(f"缺歌词: {no_lrc}\n\n")
        for label_key in [("缺标题", 1), ("缺艺术家", 2), ("缺歌词", 3)]:
            label, idx = label_key
            items = [fp for fp, t, a, l in all_files if (t, a, l)[idx-1]]
            if items:
                f.write(f"[{label}] {len(items)} 个:\n")
                for fp in items:
                    f.write(f"  {fp}\n")
                f.write("\n")
        f.write(f"--- 报告结束 ---\n")
    log(f"  完整报告已保存: {report_fn}")


# ═══════════════════════════════════════════
#  合辑标记 — 检测多歌手专辑并加 COMPILATION 标签
# ═══════════════════════════════════════════

def find_compilations(root_dir, log=None):
    """扫描目录, 找出同一专辑有多位歌手的合辑 (包括已标记的)
    返回 list of dict
    """
    dirs = {}
    if log: log(f"扫描目录: {root_dir}")
    for fp in collect_audio_files(root_dir):
        dirs.setdefault(os.path.dirname(fp), []).append(fp)
    if log: log(f"  共 {sum(len(v) for v in dirs.values())} 个文件, {len(dirs)} 个子目录")

    results = []
    for idx, (d, files) in enumerate(dirs.items()):
        if not files: continue
        if log and (idx % 10 == 0 or idx == len(dirs) - 1):
            log(f"  检测进度: {idx+1}/{len(dirs)} ({os.path.basename(d)[:30]})")
        album_name = None; artists = {}; file_infos = []; all_same_album = True
        for fp in files:
            tags = read_tags(fp)
            al = tags.get('ALBUM', '') or tags.get('TALB', '')
            ar = tags.get('ARTIST', '') or tags.get('TPE1', '')
            cp = tags.get('COMPILATION', '')
            if al:
                if album_name is None: album_name = al
                elif al != album_name: all_same_album = False
            artists[ar] = artists.get(ar, 0) + 1
            file_infos.append({'path': fp, 'name': os.path.basename(fp),
                               'artist': ar, 'album': al, 'compilation': cp})
        if all_same_album and len(artists) > 1 and album_name:
            need_tag = [f for f in file_infos if not f['compilation']]
            results.append({'dir': d, 'album': album_name,
                            'artists': list(artists.keys()),
                            'files': file_infos,
                            'need_tag_count': len(need_tag),
                            'tagged_count': len(file_infos) - len(need_tag),
                            'total': len(file_infos)})
    if log:
        need = sum(r['need_tag_count'] for r in results)
        done = sum(r['tagged_count'] for r in results)
        log(f"  扫描完成: {len(results)} 张合辑 — 已标记 {done}, 待标记 {need}")
    return results


def apply_compilation_tags(result, log=None):
    """对检测结果中的文件写入 COMPILATION=1 标签, 并刷新文件状态"""
    fixed = 0
    for fi in result['files']:
        if fi['compilation']: continue
        ext = os.path.splitext(fi['path'])[1].lower()
        try:
            if ext == '.flac':
                write_flac_tags(fi['path'], {'COMPILATION': '1'})
                fi['compilation'] = '1'; fixed += 1
                if log: log(f"    [FLAC] {fi['name'][:40]} [{fi['artist'][:15]}]")
            elif ext == '.mp3':
                import mutagen.id3, mutagen.mp3
                mp3 = mutagen.mp3.MP3(fi['path'])
                if mp3.tags is None: mp3.tags = mutagen.id3.ID3()
                mp3.tags.add(mutagen.id3.TXXX(encoding=3, desc='COMPILATION', text='1'))
                mp3.save()
                fi['compilation'] = '1'; fixed += 1
                if log: log(f"    [MP3] {fi['name'][:40]} [{fi['artist'][:15]}]")
        except Exception as e:
            if log: log(f"    [失败] {fi['name'][:40]}: {e}")
    result['need_tag_count'] = max(0, result['need_tag_count'] - fixed)
    result['tagged_count'] = result.get('tagged_count', 0) + fixed
    if log: log(f"  完成: 标记 {fixed} 个")
    return fixed


def apply_album_artist(result, album_artist, log=None):
    """对检测结果中的文件写入 ALBUMARTIST 标签"""
    fixed = 0
    for fi in result['files']:
        ext = os.path.splitext(fi['path'])[1].lower()
        try:
            if ext == '.flac':
                # 读取现有标签检查是否需要更新
                tags = read_tags(fi['path'])
                if tags.get('ALBUMARTIST', '') == album_artist: continue
                write_flac_tags(fi['path'], {'ALBUMARTIST': album_artist})
                fixed += 1
                if log: log(f"    [FLAC] {fi['name'][:40]} [{fi['artist'][:15]}]")
            elif ext == '.mp3':
                import mutagen.id3, mutagen.mp3
                mp3 = mutagen.mp3.MP3(fi['path'])
                if mp3.tags is None: continue
                # 检查是否已有相同值
                existing = str(mp3.tags.get('TPE2', ''))
                if existing == album_artist: continue
                mp3.tags.delall('TPE2')
                mp3.tags.add(mutagen.id3.TPE2(encoding=3, text=album_artist))
                mp3.save()
                fixed += 1
                if log: log(f"    [MP3] {fi['name'][:40]} [{fi['artist'][:15]}]")
        except Exception as e:
            if log: log(f"    [失败] {fi['name'][:40]}: {e}")
    if log: log(f"  完成: 设置 ALBUMARTIST={album_artist} ({fixed} 个)")
    return fixed


# ═══════════════════════════════════════════
#  GUI 数据模型
# ═══════════════════════════════════════════

class SongItem:
    """单曲状态 — 批处理管线跟踪"""
    __slots__ = ('filepath', 'filename', 'title', 'artist', 'has_lyrics',
                 'checked', 'method', 'status', 'online_results', 'online_selected',
                 'lrc_text', 'log')
    def __init__(self, filepath):
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.title = ""
        self.artist = ""
        self.has_lyrics = False
        self.checked = True
        self.method = "auto"
        self.status = "pending"
        self.online_results = []
        self.online_selected = None
        self.lrc_text = None
        self.log = []


# ═══════════════════════════════════════════
#  GUI (Tkinter) — 三标签页布局
# ═══════════════════════════════════════════

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, scrolledtext, messagebox
    import queue

    root = tk.Tk()
    root.title(f"MusicTools v{VERSION}")
    root.geometry("1000x740")
    root.minsize(800, 600)

    # ── ttk 主题 ──
    style = ttk.Style()
    for theme in ('vista', 'clam', 'alt', 'default'):
        if theme in style.theme_names():
            style.theme_use(theme); break
    style.configure('Green.TLabel', foreground='green'); style.configure('Red.TLabel', foreground='red')
    style.configure('Bold.TLabel', font=('', 9, 'bold'))

    nb = ttk.Notebook(root, padding=5)
    nb.pack(fill='both', expand=True)

    # ═══════════════════════════════════════
    #  Tab 1: 标签 / 修复 / 检测
    # ═══════════════════════════════════════
    tab1 = ttk.Frame(nb)
    nb.add(tab1, text=" 标签/修复/检测 ")

    d1_var = tk.StringVar(value=os.getcwd())
    m1_var = tk.StringVar(value="tag")

    f1 = ttk.Frame(tab1, padding=8)
    f1.pack(fill='x')
    ttk.Label(f1, text="目录:").pack(side='left')
    ttk.Entry(f1, textvariable=d1_var, width=55).pack(side='left', padx=5, fill='x', expand=True)
    ttk.Button(f1, text="浏览...", command=lambda: d1_var.set(filedialog.askdirectory())).pack(side='left')

    f1m = ttk.LabelFrame(tab1, text="功能", padding=8)
    f1m.pack(fill='x', padx=8, pady=5)
    for t, v in [("tag   — 从文件名写入标签", "tag"),
                 ("fix   — 修复 FLAC PICTURE 封面", "fix"),
                 ("check — 检测缺失标签/歌词", "check")]:
        ttk.Radiobutton(f1m, text=t, variable=m1_var, value=v).pack(anchor='w')

    f1o = ttk.LabelFrame(tab1, text="输出", padding=5)
    f1o.pack(fill='both', expand=True, padx=8, pady=5)
    t1_out = scrolledtext.ScrolledText(f1o, wrap='word', height=14, font=('Consolas', 9))
    t1_out.pack(fill='both', expand=True)

    def t1_log(msg):
        t1_out.insert('end', msg + '\n'); t1_out.see('end'); t1_out.update()

    def t1_run():
        d = d1_var.get().strip()
        if not d or not os.path.isdir(d): messagebox.showerror("错误", "请选择有效目录"); return
        t1_out.delete('1.0', 'end')
        t1_log(f"{'='*50}\n  MusicTools v{VERSION}  [{m1_var.get()}]\n{'='*50}")
        try:
            {'tag': cmd_tag, 'fix': cmd_fix, 'check': cmd_check}[m1_var.get()](d, t1_log)
        except Exception as e:
            t1_log(f"\n[错误] {e}"); import traceback; t1_log(traceback.format_exc())
        t1_log(f"\n{'='*50}\n完成!")

    ttk.Button(tab1, text=" 执 行 ",
               command=lambda: threading.Thread(target=t1_run, daemon=True).start()).pack(pady=8)

    # ═══════════════════════════════════════
    #  Tab 2: 单曲歌词搜索
    # ═══════════════════════════════════════
    tab2 = ttk.Frame(nb)
    nb.add(tab2, text=" 单曲歌词 ")

    d2_var = tk.StringVar(value=os.getcwd())
    f2 = ttk.Frame(tab2, padding=8)
    f2.pack(fill='x')
    ttk.Label(f2, text="目录:").pack(side='left')
    ttk.Entry(f2, textvariable=d2_var, width=45).pack(side='left', padx=5, fill='x', expand=True)
    ttk.Button(f2, text="浏览...", command=lambda: d2_var.set(filedialog.askdirectory())).pack(side='left')
    ttk.Button(f2, text="扫描", command=lambda: t2_scan()).pack(side='left', padx=3)

    # 文件列表 + 元数据
    f2m = ttk.Frame(tab2, padding=5)
    f2m.pack(fill='x')
    ttk.Label(f2m, text="文件:").pack(side='left')
    t2_file_cb = ttk.Combobox(f2m, state='readonly', width=45)
    t2_file_cb.pack(side='left', padx=5, fill='x', expand=True)
    t2_info = tk.StringVar(value="")
    ttk.Label(f2m, textvariable=t2_info).pack(side='left', padx=10)

    # 操作按钮
    f2b = ttk.Frame(tab2, padding=5)
    f2b.pack(fill='x')
    ttk.Button(f2b, text="在线搜索", command=lambda: threading.Thread(target=t2_search, daemon=True).start()).pack(side='left', padx=2)
    ttk.Button(f2b, text="LRCMaker (粘贴歌词)", command=lambda: t2_lrcmaker()).pack(side='left', padx=2)
    ttk.Button(f2b, text="zimu 自动识别", command=lambda: threading.Thread(target=t2_zimu, daemon=True).start()).pack(side='left', padx=2)

    # 在线搜索结果表格
    f2t = ttk.LabelFrame(tab2, text="搜索结果 (双击下载)", padding=3)
    f2t.pack(fill='both', expand=True, padx=8, pady=3)
    t2_tree = ttk.Treeview(f2t, columns=('src', 'name', 'artist', 'score', 'album'),
                           show='headings', height=8)
    t2_tree.heading('src', text='源'); t2_tree.column('src', width=55, anchor='center')
    t2_tree.heading('name', text='歌名'); t2_tree.column('name', width=180)
    t2_tree.heading('artist', text='歌手'); t2_tree.column('artist', width=120)
    t2_tree.heading('score', text='评分'); t2_tree.column('score', width=50, anchor='center')
    t2_tree.heading('album', text='专辑'); t2_tree.column('album', width=160)
    t2_tree.pack(side='left', fill='both', expand=True)
    t2_sb = ttk.Scrollbar(f2t, orient='vertical', command=t2_tree.yview)
    t2_tree.configure(yscrollcommand=t2_sb.set); t2_sb.pack(side='right', fill='y')

    # 日志
    f2l = ttk.LabelFrame(tab2, text="输出", padding=3)
    f2l.pack(fill='both', padx=8, pady=3)
    t2_out = scrolledtext.ScrolledText(f2l, wrap='word', height=6, font=('Consolas', 9))
    t2_out.pack(fill='both', expand=True)

    def t2_log(msg):
        t2_out.insert('end', str(msg) + '\n'); t2_out.see('end'); t2_out.update()

    t2_files = []; t2_cur_fp = None; t2_cur_top = []

    def t2_scan():
        nonlocal t2_files, t2_cur_fp, t2_cur_top
        d = d2_var.get().strip()
        if not os.path.isdir(d): return
        t2_files = collect_audio_files(d)
        t2_cur_fp = None; t2_cur_top = []
        t2_file_cb['values'] = [os.path.basename(f) for f in t2_files]
        t2_tree.delete(*t2_tree.get_children())
        t2_log(f"扫描: {len(t2_files)} 个文件")
        if t2_files:
            t2_file_cb.current(0); t2_select_file()

    def t2_select_file(*_):
        nonlocal t2_cur_fp, t2_cur_top
        idx = t2_file_cb.current()
        if idx < 0 or idx >= len(t2_files): return
        t2_cur_fp = t2_files[idx]; t2_cur_top = []
        title, artist = resolve_song_metadata(t2_cur_fp)
        has_lrc = has_lyrics(t2_cur_fp)
        t2_info.set(f"{title[:20]} — {artist[:15]} {'[有歌词]' if has_lrc else '[缺歌词]'}")
        t2_tree.delete(*t2_tree.get_children())

    t2_file_cb.bind('<<ComboboxSelected>>', t2_select_file)

    def t2_search():
        if not t2_cur_fp: return
        title, artist = resolve_song_metadata(t2_cur_fp)
        t2_log(f"搜索: {title} — {artist}")
        results = search_both(title, artist, 4)
        t2_cur_top = results
        t2_tree.delete(*t2_tree.get_children())
        for r in results:
            t2_tree.insert('', 'end', values=(r['src'], r['name'][:35], r['artist'][:22],
                           f"{r['score']:.2f}", r.get('album', '')[:30]))

    def t2_download(event):
        sel = t2_tree.selection()
        if not sel or not t2_cur_fp: return
        idx = t2_tree.index(sel[0])
        if idx >= len(t2_cur_top): return
        r = t2_cur_top[idx]
        t2_log(f"下载: [{r['src']}] {r['name'][:30]}")
        lrc = fetch_lyric_by_result(r)
        if lrc:
            embed_lyrics_to_file(t2_cur_fp, lrc)
            t2_log(f"  保存完成 ({len(lrc)} 字符)")
            t2_select_file()
        else:
            t2_log(f"  无歌词数据")

    t2_tree.bind('<Double-1>', t2_download)

    def t2_lrcmaker():
        if not t2_cur_fp: return
        dlg = tk.Toplevel(root); dlg.title("LRCMaker — 粘贴歌词"); dlg.geometry("650x420")
        dlg.transient(root); dlg.grab_set(); dlg.minsize(400, 250)
        dlg.grid_rowconfigure(0, weight=1); dlg.grid_columnconfigure(0, weight=1)
        txt = tk.Text(dlg, font=('Consolas', 10), wrap='word')
        txt.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        sb = ttk.Scrollbar(dlg, orient='vertical', command=txt.yview)
        sb.grid(row=0, column=1, sticky='ns', pady=5)
        txt.configure(yscrollcommand=sb.set)
        btn_frm = ttk.Frame(dlg)
        btn_frm.grid(row=1, column=0, columnspan=2, pady=3)
        ttk.Button(btn_frm, text="预处理 (空格拆句)", command=lambda: _preprocess(txt)).pack(side='left', padx=2)
        ttk.Button(btn_frm, text="开始对齐", command=lambda: threading.Thread(target=do, daemon=True).start()).pack(side='left', padx=5)
        def _preprocess(w):
            text = w.get('1.0', 'end-1c')
            if text.strip():
                result = preprocess_lyrics_text(text)
                w.delete('1.0', 'end'); w.insert('1.0', result)
        def do():
            text = txt.get('1.0', 'end-1c').strip()
            if not text: t2_log("无歌词文本"); dlg.destroy(); return
            title, artist = resolve_song_metadata(t2_cur_fp)
            album = os.path.basename(os.path.dirname(t2_cur_fp))
            t2_log(f"LRCMaker 对齐: {title}")
            lrc = ai_align_with_lrcmaker(t2_cur_fp, text, title, artist, album)
            if lrc:
                embed_lyrics_to_file(t2_cur_fp, lrc); t2_log(f"  完成 ({len(lrc)} 字符)"); t2_select_file()
            else: t2_log("  对齐失败")
            dlg.destroy()

    def t2_zimu():
        if not t2_cur_fp: return
        t2_log("zimu 语音识别中...")
        data = zimu_transcribe(t2_cur_fp)
        if not data: t2_log("  识别失败"); return
        lrc = zimu_build_lrc(data)
        if lrc:
            embed_lyrics_to_file(t2_cur_fp, lrc, word_lrc=True)
            t2_log(f"  完成 ({len(lrc)} 字符, {lrc.count(chr(10))+1} 行)"); t2_select_file()
        else: t2_log("  构建失败")

    # ═══════════════════════════════════════
    #  Tab 3: 批量歌词处理
    # ═══════════════════════════════════════
    tab3 = ttk.Frame(nb)
    nb.add(tab3, text=" 批量歌词 ")

    # 工具栏
    f3t = ttk.Frame(tab3, padding=5)
    f3t.pack(fill='x')
    d3_var = tk.StringVar(value=os.getcwd())
    ttk.Label(f3t, text="目录:").pack(side='left')
    ttk.Entry(f3t, textvariable=d3_var, width=40).pack(side='left', padx=5, fill='x', expand=True)
    ttk.Button(f3t, text="浏览...", command=lambda: d3_var.set(filedialog.askdirectory())).pack(side='left', padx=2)
    ttk.Button(f3t, text="扫描", command=lambda: t3_scan()).pack(side='left', padx=2)

    t3_lr_label = ttk.Label(f3t, text="LRC: ?", width=8); t3_lr_label.pack(side='left', padx=5)
    t3_zm_label = ttk.Label(f3t, text="zimu: ?", width=8); t3_zm_label.pack(side='left', padx=2)

    ttk.Button(f3t, text="检查AI", command=lambda: threading.Thread(target=t3_check_ai, daemon=True).start()).pack(side='left', padx=3)
    ttk.Button(f3t, text="文件检测", command=lambda: threading.Thread(target=t3_run_check, daemon=True).start()).pack(side='left', padx=3)
    ttk.Button(f3t, text="预搜(手动)", command=lambda: threading.Thread(target=t3_presearch, daemon=True).start()).pack(side='left', padx=3)
    t3_start_btn = ttk.Button(f3t, text="▶ 开始批处理", command=lambda: t3_start())
    t3_start_btn.pack(side='right', padx=2)
    t3_stop_btn = ttk.Button(f3t, text="停止", command=lambda: t3_stop(), state='disabled')
    t3_stop_btn.pack(side='right', padx=2)

    # 统计栏
    t3_stat_var = tk.StringVar(value="点击'检查AI'确认后端状态, 点击'扫描'加载文件")
    ttk.Label(tab3, textvariable=t3_stat_var, padding=3).pack(fill='x')

    # 歌曲列表 Treeview
    f3tv = ttk.Frame(tab3)
    f3tv.pack(fill='both', expand=True, padx=5)
    t3_tree = ttk.Treeview(f3tv, columns=('checked', 'filename', 'title', 'artist', 'method', 'status'),
                           show='headings', height=14)
    t3_tree.heading('checked', text='勾选'); t3_tree.column('checked', width=40, anchor='center')
    t3_tree.heading('filename', text='文件名'); t3_tree.column('filename', width=200)
    t3_tree.heading('title', text='标题'); t3_tree.column('title', width=120)
    t3_tree.heading('artist', text='歌手'); t3_tree.column('artist', width=100)
    t3_tree.heading('method', text='处理方式'); t3_tree.column('method', width=80, anchor='center')
    t3_tree.heading('status', text='状态'); t3_tree.column('status', width=70, anchor='center')
    t3_tree.pack(side='left', fill='both', expand=True)
    t3_sb = ttk.Scrollbar(f3tv, orient='vertical', command=t3_tree.yview)
    t3_tree.configure(yscrollcommand=t3_sb.set); t3_sb.pack(side='right', fill='y')

    # 批量设置
    f3b = ttk.Frame(tab3, padding=3)
    f3b.pack(fill='x')
    ttk.Label(f3b, text="设置选中项为:").pack(side='left')
    for m, lbl in [("auto", "自动"), ("manual", "手动"), ("zimu", "zimu"), ("lrcmaker", "LRCMaker"), ("skip", "跳过")]:
        ttk.Button(f3b, text=lbl, command=lambda m=m: t3_set_method(m), width=8).pack(side='left', padx=2)
    ttk.Separator(f3b, orient='vertical').pack(side='left', padx=8, fill='y')
    ttk.Button(f3b, text="全选", command=lambda: t3_select_all(True)).pack(side='left', padx=2)
    ttk.Button(f3b, text="全不选", command=lambda: t3_select_all(False)).pack(side='left', padx=2)

    # LRCMaker 文本区域 (可调大小, *** 分隔歌曲, --- 分隔元数据)
    f3lrc = ttk.LabelFrame(tab3, text="LRCMaker 歌词文本 — *** 分隔歌曲 | --- 分隔元数据(标题/歌手/专辑)", padding=3)
    f3lrc.pack(fill='both', padx=5, pady=3)
    f3lrc.grid_rowconfigure(0, weight=1); f3lrc.grid_columnconfigure(0, weight=1)
    t3_lrc_text = tk.Text(f3lrc, height=12, font=('Consolas', 9), wrap='word')
    t3_lrc_text.grid(row=0, column=0, sticky='nsew')
    t3_lrc_sb = ttk.Scrollbar(f3lrc, orient='vertical', command=t3_lrc_text.yview)
    t3_lrc_sb.grid(row=0, column=1, sticky='ns')
    t3_lrc_text.configure(yscrollcommand=t3_lrc_sb.set)
    # 按钮行
    t3_lrc_btn = ttk.Frame(f3lrc)
    t3_lrc_btn.grid(row=1, column=0, columnspan=2, sticky='ew', pady=2)
    ttk.Button(t3_lrc_btn, text="预处理 (空格拆句)", command=lambda: t3_preprocess()).pack(side='left', padx=2)
    ttk.Button(t3_lrc_btn, text="预处理 (双语)", command=lambda: t3_preprocess(bilingual=True)).pack(side='left', padx=2)
    ttk.Button(t3_lrc_btn, text="修复FLAC封面", command=lambda: threading.Thread(target=t3_fix_flac, daemon=True).start()).pack(side='left', padx=2)
    ttk.Button(f3lrc, text="﹀ 展开 / 收起 ﹀", command=lambda: t3_toggle_lrc()).grid(row=2, column=0, columnspan=2, pady=1)
    t3_lrc_text_shown = True
    def t3_toggle_lrc():
        nonlocal t3_lrc_text_shown
        t3_lrc_text_shown = not t3_lrc_text_shown
        if t3_lrc_text_shown:
            t3_lrc_text.grid(); t3_lrc_sb.grid(); t3_lrc_btn.grid()
        else:
            t3_lrc_text.grid_remove(); t3_lrc_sb.grid_remove(); t3_lrc_btn.grid_remove()

    # 进度 + 日志
    f3p = ttk.Frame(tab3, padding=3)
    f3p.pack(fill='x')
    t3_bar = ttk.Progressbar(f3p, mode='determinate'); t3_bar.pack(side='left', fill='x', expand=True, padx=3)
    t3_prog_var = tk.StringVar(value="")
    ttk.Label(f3p, textvariable=t3_prog_var, width=30).pack(side='right')

    f3l = ttk.LabelFrame(tab3, text="日志", padding=3)
    f3l.pack(fill='both', padx=5, pady=3)
    t3_out = scrolledtext.ScrolledText(f3l, wrap='word', height=8, font=('Consolas', 9))
    t3_out.pack(fill='both', expand=True)

    # ── Tab3 状态 ──
    t3_songs = []; t3_state_running = False; t3_state_stop = False
    t3_log_queue = queue.Queue()

    def t3_log(msg):
        t3_log_queue.put(str(msg))

    def t3_preprocess(bilingual=False):
        """预处理歌词文本"""
        text = t3_lrc_text.get('1.0', 'end-1c')
        if not text.strip(): return
        result = preprocess_lyrics_text(text, bilingual)
        t3_lrc_text.delete('1.0', 'end')
        t3_lrc_text.insert('1.0', result)
        t3_log(f"  预处理完成 ({'双语' if bilingual else '通用'}模式)")

    def t3_fix_flac():
        """修复当前目录下的 FLAC PICTURE 元数据"""
        d = d3_var.get().strip()
        if not os.path.isdir(d): return
        t3_log(f"  修复 FLAC 封面: {d}")
        cmd_fix(d, t3_log)

    def t3_check_ai():
        lr = check_lrcmaker(); zm = check_zimu()
        t3_lr_label.config(text=f"LRC: {'ON' if lr else 'OFF'}", foreground='green' if lr else 'red')
        t3_zm_label.config(text=f"zimu: {'ON' if zm else 'OFF'}", foreground='green' if zm else 'red')
        t3_log(f"AI 状态: LRCMaker={'ON' if lr else 'OFF'}, zimu={'ON' if zm else 'OFF'}")

    def t3_run_check():
        d = d3_var.get().strip()
        if not os.path.isdir(d):
            t3_log(f"目录不存在: {d}")
            return
        t3_log(f"文件检测: {d}")
        cmd_check(d, t3_log)

    def t3_scan():
        nonlocal t3_songs
        d = d3_var.get().strip()
        if not os.path.isdir(d): return
        files = collect_audio_files(d)
        t3_songs = []
        for fp in files:
            si = SongItem(fp)
            si.has_lyrics = has_lyrics(fp)
            title, artist = resolve_song_metadata(fp)
            si.title = title; si.artist = artist
            if si.has_lyrics:
                si.method = "skip"; si.status = "skipped"
            t3_songs.append(si)
        t3_refresh_tree()
        need = sum(1 for s in t3_songs if not s.has_lyrics)
        has = len(t3_songs) - need
        t3_label_var.set(f"共 {len(t3_songs)} 文件 | 已有歌词: {has} | 缺歌词: {need}")

    def t3_refresh_tree():
        t3_tree.delete(*t3_tree.get_children())
        for i, s in enumerate(t3_songs):
            chk = "[x]" if s.checked else "[ ]"
            status_icon = {'pending': '-', 'processing': '⟳', 'done': '✓', 'failed': '✗', 'skipped': '—'}.get(s.status, s.status)
            t3_tree.insert('', 'end', iid=f"I{i}", values=(chk, s.filename[:50], s.title[:25],
                          s.artist[:20], s.method, status_icon))

    # 勾选切换
    def t3_on_click(event):
        col = t3_tree.identify_column(event.x)
        if col != '#1': return
        row = t3_tree.identify_row(event.y)
        if not row: return
        idx = int(row[1:])
        if 0 <= idx < len(t3_songs):
            t3_songs[idx].checked = not t3_songs[idx].checked
            t3_tree.set(row, 'checked', "[x]" if t3_songs[idx].checked else "[ ]")

    t3_tree.bind('<Button-1>', t3_on_click)

    # 内联编辑处理方式
    def t3_on_method(event):
        col = t3_tree.identify_column(event.x)
        if col != '#5': return
        row = t3_tree.identify_row(event.y)
        if not row: return
        idx = int(row[1:])
        if not (0 <= idx < len(t3_songs)): return
        x, y, w, h = t3_tree.bbox(row, col)
        cb = ttk.Combobox(t3_tree, values=["auto", "manual", "zimu", "lrcmaker", "skip"], state='readonly', width=12)
        cb.place(x=x, y=y, width=w+10, height=h)
        cb.set(t3_songs[idx].method)
        def done(*_):
            t3_songs[idx].method = cb.get()
            t3_tree.set(row, 'method', cb.get())
            cb.destroy()
        cb.bind('<<ComboboxSelected>>', done)
        cb.bind('<FocusOut>', done)
        cb.focus_set()

    t3_tree.bind('<Double-1>', t3_on_method)

    def t3_set_method(method):
        for i, s in enumerate(t3_songs):
            if s.checked and not s.has_lyrics:
                s.method = method
        t3_refresh_tree()

    def t3_select_all(sel):
        for s in t3_songs:
            if not s.has_lyrics: s.checked = sel
        t3_refresh_tree()

    # ── 批处理线程 ──
    def t3_start():
        nonlocal t3_state_running, t3_state_stop
        if t3_state_running: return
        to_process = [s for s in t3_songs if s.checked and not s.has_lyrics]
        if not to_process:
            messagebox.showinfo("提示", "没有需要处理的歌曲"); return
        t3_state_running = True; t3_state_stop = False
        t3_start_btn.config(state='disabled'); t3_stop_btn.config(state='normal')
        t3_bar['maximum'] = len(to_process); t3_bar['value'] = 0
        t3_prog_var.set(f"0/{len(to_process)}")
        t3_out.delete('1.0', 'end')
        threading.Thread(target=t3_worker, args=(to_process,), daemon=True).start()

    def t3_stop():
        nonlocal t3_state_stop
        t3_state_stop = True; t3_log("[用户] 停止请求...")

    def t3_presearch():
        """预搜索所有手动模式歌曲, 弹出选择对话框"""
        import tkinter.simpledialog
        manual_songs = [s for s in t3_songs if s.checked and not s.has_lyrics and s.method == "manual"]
        if not manual_songs:
            t3_log("没有手动模式的歌曲需要预搜索"); return
        for song in manual_songs:
            t3_log(f"搜索: {song.filename[:40]}")
            title, artist = resolve_song_metadata(song.filepath)
            results = search_both(title, artist, 4)
            if not results:
                t3_log(f"  无搜索结果, 跳过"); continue
            # 弹窗让用户选择
            sel = t3_pick_result_dialog(song.filename, title, results)
            if sel is not None:
                song.online_selected = results[sel]
                t3_log(f"  已选 [{results[sel]['src']}] {results[sel]['name'][:25]}")
            else:
                t3_log(f"  用户跳过")

    def t3_pick_result_dialog(fn, title, results):
        """搜索结果选择弹窗, 返回选中结果索引, 取消返回 None"""
        dlg = tk.Toplevel(root)
        dlg.title(f"选择搜索结果 — {fn[:30]}")
        dlg.geometry("650x350"); dlg.transient(root); dlg.grab_set()
        dlg.grid_rowconfigure(0, weight=1); dlg.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(dlg, columns=('src', 'name', 'artist', 'score', 'album'),
                           show='headings', height=14)
        tree.heading('src', text='来源'); tree.column('src', width=50, anchor='center')
        tree.heading('name', text='歌名'); tree.column('name', width=180)
        tree.heading('artist', text='歌手'); tree.column('artist', width=120)
        tree.heading('score', text='评分'); tree.column('score', width=50, anchor='center')
        tree.heading('album', text='专辑'); tree.column('album', width=180)
        tree.grid(row=0, column=0, columnspan=2, sticky='nsew', padx=5, pady=5)
        sb = ttk.Scrollbar(dlg, orient='vertical', command=tree.yview)
        sb.grid(row=0, column=2, sticky='ns')
        tree.configure(yscrollcommand=sb.set)
        for idx, r in enumerate(results):
            tree.insert('', 'end', values=(r['src'], r['name'][:35], r['artist'][:22],
                          f"{r['score']:.2f}", r.get('album', '')[:30]))
        sel = [None]
        def on_pick():
            sel_item = tree.selection()
            if sel_item:
                sel[0] = tree.index(sel_item[0])
            dlg.destroy()
        def on_skip():
            sel[0] = None; dlg.destroy()
        btn_f = ttk.Frame(dlg)
        btn_f.grid(row=1, column=0, columnspan=3, pady=5)
        ttk.Button(btn_f, text="确认选择", command=on_pick).pack(side='left', padx=5)
        ttk.Button(btn_f, text="跳过", command=on_skip).pack(side='left', padx=5)
        root.wait_window(dlg)
        return sel[0]

    def t3_worker(to_process):
        nonlocal t3_state_running
        # 提取 LRCMaker 文本 — 用 *** 分隔歌曲
        raw_lrc = t3_lrc_text.get('1.0', 'end-1c').strip()
        lrc_blocks = []
        for block in raw_lrc.split('\n***') + raw_lrc.split('\n***\n'):
            block = block.strip().lstrip('*').strip()
            if block:
                lrc_blocks.append(block)
        if not lrc_blocks:
            # 回退: 按空行分隔
            lrc_lines = raw_lrc.split('\n')
            cur = []
            for line in lrc_lines:
                if line.strip(): cur.append(line)
                elif cur: lrc_blocks.append('\n'.join(cur)); cur = []
            if cur: lrc_blocks.append('\n'.join(cur))

        # 解析每个块的元数据
        parsed_blocks = []
        for block in lrc_blocks:
            lines = block.split('\n')
            meta = {'title': '', 'artist': '', 'album': '', 'text': block}
            in_meta = False
            text_lines = []
            for line in lines:
                if line.strip().startswith('---'):
                    in_meta = not in_meta
                    continue
                if in_meta:
                    s = line.strip()
                    if s.lower().startswith('title:') or s.startswith('标题:'):
                        meta['title'] = s.split(':', 1)[-1].strip()
                    elif s.lower().startswith('artist:') or s.startswith('歌手:') or s.startswith('作者:'):
                        meta['artist'] = s.split(':', 1)[-1].strip()
                    elif s.lower().startswith('album:') or s.startswith('专辑:'):
                        meta['album'] = s.split(':', 1)[-1].strip()
                else:
                    text_lines.append(line)
            meta['text'] = '\n'.join(text_lines).strip()
            parsed_blocks.append(meta)

        done = 0; total = len(to_process)
        for i, song in enumerate(to_process):
            if t3_state_stop: break
            title, artist = resolve_song_metadata(song.filepath)
            song.title = title; song.artist = artist
            song.status = "processing"; t3_log(f"[{time.strftime('%H:%M:%S')}] {song.filename[:45]} — {song.method}")

            lrc = None
            try:
                if song.method == "auto":
                    results = search_both(title, artist, 4)
                    song.online_results = results
                    if results and results[0]['score'] >= 0.5:
                        r = results[0]
                        t3_log(f"  [{r['src']}] {r['name'][:30]} — {r['artist'][:20]} (score:{r['score']:.2f})")
                        lrc = fetch_lyric_by_result(r)
                        if lrc: t3_log(f"  下载 {len(lrc)} 字符")
                    else:
                        t3_log(f"  无高置信度匹配")

                elif song.method == "manual":
                    # 使用预搜索结果 (如有)
                    r = song.online_selected
                    if r:
                        t3_log(f"  [{r['src']}] {r['name'][:30]} — {r['artist'][:20]}")
                        lrc = fetch_lyric_by_result(r)
                        if lrc: t3_log(f"  下载 {len(lrc)} 字符")
                    else:
                        t3_log(f"  未选择搜索结果, 跳过")

                elif song.method == "zimu":
                    data = zimu_transcribe(song.filepath)
                    if data: lrc = zimu_build_lrc(data)
                    if lrc: t3_log(f"  识别 {len(lrc)} 字符, {lrc.count(chr(10))+1} 行")

                elif song.method == "lrcmaker":
                    lrc_idx = len([s for s in to_process[:i] if s.method == "lrcmaker"])
                    if lrc_idx < len(parsed_blocks):
                        pm = parsed_blocks[lrc_idx]
                        text = pm['text']
                        t = pm['title'] or title
                        a = pm['artist'] or artist
                        al = pm['album'] or os.path.basename(os.path.dirname(song.filepath))
                        t3_log(f"  [{t[:20]}] 对齐中...")
                        lrc = ai_align_with_lrcmaker(song.filepath, text, t, a, al)
                        if lrc: t3_log(f"  对齐完成 {len(lrc)} 字符")
                    else:
                        t3_log(f"  缺歌词文本 (第 {lrc_idx+1} 个LRCMaker任务)")

                if lrc:
                    embed_lyrics_to_file(song.filepath, lrc, word_lrc=(song.method == "zimu"))
                    song.lrc_text = lrc; song.status = "done"
                    t3_log(f"  已保存")
                else:
                    song.status = "failed"; t3_log(f"  失败")
            except Exception as e:
                song.status = "failed"; t3_log(f"  异常: {e}")

            done += 1; t3_log_queue.put(("progress", done, total))

        t3_log_queue.put(("done",)); t3_state_running = False

    # ── 日志/进度 轮询 ──
    def t3_poll():
        try:
            while True:
                item = t3_log_queue.get_nowait()
                if item == ("done",):
                    t3_bar['value'] = t3_bar['maximum']
                    t3_start_btn.config(state='normal'); t3_stop_btn.config(state='disabled')
                    t3_out.insert('end', "\n=== 批处理完成 ===\n"); t3_out.see('end')
                    t3_refresh_tree()
                elif isinstance(item, tuple) and item[0] == "progress":
                    _, d, total = item
                    t3_bar['value'] = d; t3_prog_var.set(f"{d}/{total}"); t3_refresh_tree()
                else:
                    t3_out.insert('end', str(item) + '\n'); t3_out.see('end')
        except queue.Empty: pass
        root.after(100, t3_poll)

    t3_poll()

    # ═══════════════════════════════════════
    #  Tab 4: FLAC 智能压缩 (192kHz → 96kHz)
    # ═══════════════════════════════════════
    tab4 = ttk.Frame(nb)
    nb.add(tab4, text=" FLAC压缩 ")

    f4t = ttk.Frame(tab4, padding=5)
    f4t.pack(fill='x')
    d4_var = tk.StringVar(value=os.getcwd())
    ttk.Label(f4t, text="目录:").pack(side='left')
    ttk.Entry(f4t, textvariable=d4_var, width=40).pack(side='left', padx=5, fill='x', expand=True)
    ttk.Button(f4t, text="浏览...", command=lambda: d4_var.set(filedialog.askdirectory())).pack(side='left', padx=2)
    ttk.Button(f4t, text="扫描192kHz", command=lambda: threading.Thread(target=t4_scan, daemon=True).start()).pack(side='left', padx=2)

    f4s = ttk.Frame(tab4, padding=3)
    f4s.pack(fill='x')
    ttk.Label(f4s, text="目标采样率:").pack(side='left')
    t4_sr_var = tk.StringVar(value="96000")
    t4_sr_cb = ttk.Combobox(f4s, textvariable=t4_sr_var, values=["96000", "48000", "44100"], width=8, state='readonly')
    t4_sr_cb.pack(side='left', padx=3)
    ttk.Label(f4s, text="位深:").pack(side='left', padx=(10,0))
    t4_bits_var = tk.StringVar(value="16")
    ttk.Combobox(f4s, textvariable=t4_bits_var, values=["16", "24"], width=5, state='readonly').pack(side='left', padx=3)
    t4_compress_btn = ttk.Button(f4s, text="开始压缩", command=lambda: threading.Thread(target=t4_compress, daemon=True).start())
    t4_compress_btn.pack(side='right', padx=3)

    f4tv = ttk.Frame(tab4)
    f4tv.pack(fill='both', expand=True, padx=5)
    t4_tree = ttk.Treeview(f4tv, columns=('checked', 'filename', 'sr', 'bps', 'size', 'estimate'),
                           show='headings', height=12)
    t4_tree.heading('checked', text='勾选'); t4_tree.column('checked', width=40, anchor='center')
    t4_tree.heading('filename', text='文件名'); t4_tree.column('filename', width=220)
    t4_tree.heading('sr', text='采样率'); t4_tree.column('sr', width=70, anchor='center')
    t4_tree.heading('bps', text='位深'); t4_tree.column('bps', width=50, anchor='center')
    t4_tree.heading('size', text='当前大小'); t4_tree.column('size', width=80, anchor='center')
    t4_tree.heading('estimate', text='预计压缩后'); t4_tree.column('estimate', width=90, anchor='center')
    t4_tree.pack(side='left', fill='both', expand=True)
    t4_sb = ttk.Scrollbar(f4tv, orient='vertical', command=t4_tree.yview)
    t4_tree.configure(yscrollcommand=t4_sb.set); t4_sb.pack(side='right', fill='y')

    f4b = ttk.Frame(tab4, padding=3)
    f4b.pack(fill='x')
    ttk.Button(f4b, text="全选", command=lambda: t4_sel(True)).pack(side='left', padx=2)
    ttk.Button(f4b, text="全不选", command=lambda: t4_sel(False)).pack(side='left', padx=2)

    t4_bar = ttk.Progressbar(tab4, mode='determinate')
    t4_bar.pack(fill='x', padx=5, pady=2)

    f4l = ttk.LabelFrame(tab4, text="日志", padding=3)
    f4l.pack(fill='both', padx=5, pady=3)
    t4_out = scrolledtext.ScrolledText(f4l, wrap='word', height=5, font=('Consolas', 9))
    t4_out.pack(fill='both', expand=True)

    t4_files = []; t4_log_queue = queue.Queue()

    def t4_log(msg):
        t4_log_queue.put(str(msg))

    def t4_scan():
        nonlocal t4_files
        d = d4_var.get().strip()
        if not os.path.isdir(d): return
        t4_files = []
        for r, _, fs in os.walk(d):
            for f in fs:
                if f.lower().endswith('.flac'):
                    t4_files.append(os.path.join(r, f))
        t4_files.sort()
        t4_tree.delete(*t4_tree.get_children())
        for i, fp in enumerate(t4_files):
            try:
                import soundfile as sf
                info = sf.info(fp)
                if info.samplerate >= 192000:
                    bps_val = _get_flac_bps(fp) if callable(globals().get('_get_flac_bps')) else info.subtype
                    size_mb = os.path.getsize(fp) / 1024 / 1024
                    target_sr = int(t4_sr_var.get())
                    bits = int(t4_bits_var.get())
                    est = size_mb * (target_sr / info.samplerate) * ((bits/8) / max((32/8), 1/2)) * 0.7
                    t4_files.append((fp, info.samplerate, bps_val, size_mb, est, True))
                    t4_tree.insert('', 'end', iid=f'T{i}',
                                   values=('[x]', os.path.basename(fp)[:50],
                                           f'{info.samplerate}Hz', f'{bps_val}bit',
                                           f'{size_mb:.0f}MB', f'~{est:.0f}MB'))
                else:
                    t4_files.append((fp, info.samplerate, 0, 0, 0, False))
            except Exception:
                pass
        valid = sum(1 for x in t4_files if x[5])
        t4_log(f"扫描完成: {len(t4_files)} 个FLAC, {valid} 个192kHz+")

    def _get_flac_bps(fp):
        try:
            with open(fp, 'rb') as f: data = f.read(42)
            pack = struct.unpack('>Q', data[18:26])[0]
            return ((pack >> 36) & 0x1F) + 1
        except: return 0

    def t4_sel(checked):
        for i, item in enumerate(t4_files):
            if item[5]:
                t4_tree.set(f'T{i}', 'checked', '[x]' if checked else '[ ]')

    def t4_on_click(event):
        col = t4_tree.identify_column(event.x)
        if col != '#1': return
        row = t4_tree.identify_row(event.y)
        if not row: return
        cur = t4_tree.set(row, 'checked')
        t4_tree.set(row, 'checked', '[ ]' if cur == '[x]' else '[x]')
    t4_tree.bind('<Button-1>', t4_on_click)

    def t4_compress():
        to_compress = []
        for i, item in enumerate(t4_files):
            if item[5] and t4_tree.set(f'T{i}', 'checked') == '[x]':
                to_compress.append(item)
        if not to_compress:
            t4_log("请勾选要压缩的文件"); return
        import soundfile as sf
        from scipy import signal
        from math import gcd
        try: import numpy as np
        except: pass
        target_sr = int(t4_sr_var.get()); bits = int(t4_bits_var.get())
        t4_bar['maximum'] = len(to_compress); t4_bar['value'] = 0
        total_before = total_after = 0
        for idx, item in enumerate(to_compress):
            fp, sr, bps, size_mb, est, _ = item
            fn = os.path.basename(fp)
            t4_log(f"[{idx+1}/{len(to_compress)}] {fn[:45]}")
            try:
                # Read
                audio, _ = sf.read(fp, dtype='float32', always_2d=True)
                if target_sr < sr:
                    g = gcd(sr, target_sr); up = target_sr // g; down = sr // g
                    ch = audio.shape[1]
                    res = [signal.resample_poly(audio[:, c], up, down) for c in range(ch)]
                    out_len = len(res[0])
                    out = np.empty((out_len, ch), dtype=np.float32)
                    for c in range(ch): out[:, c] = res[c][:out_len]
                    audio = out
                else: target_sr = sr
                # Rebuild
                subtype = 'PCM_24' if bits == 24 else 'PCM_16'
                tmp_p = fp + '.tmp_compress.flac'
                sf.write(tmp_p, audio, target_sr, subtype=subtype)
                with open(tmp_p, 'rb') as f: tmp_d = bytearray(f.read())
                os.remove(tmp_p)
                # Extract STREAMINFO + audio from tmp
                pos = 4; tmp_si = None; tmp_aud = None
                while pos < len(tmp_d):
                    hdr = tmp_d[pos:pos+4]; is_last = hdr[0] & 0x80; bt = hdr[0] & 0x7F
                    bl = struct.unpack('>I', b'\x00'+hdr[1:4])[0]
                    if bt == 0: tmp_si = bytes(tmp_d[pos+4:pos+4+bl])
                    pos += 4+bl
                    if is_last: tmp_aud = bytes(tmp_d[pos:]); break
                # Extract source metadata (skip STREAMINFO + SEEKTABLE)
                with open(fp, 'rb') as f: src_raw = bytearray(f.read())
                src_blocks = []
                pos2 = 4
                while pos2 < len(src_raw):
                    hdr = src_raw[pos2:pos2+4]; is_last = hdr[0] & 0x80; bt = hdr[0] & 0x7F
                    bl = struct.unpack('>I', b'\x00'+hdr[1:4])[0]; bd = src_raw[pos2+4:pos2+4+bl]
                    src_blocks.append({'type': bt, 'length': bl, 'data': bd, 'is_last': bool(is_last)})
                    pos2 += 4+bl
                    if is_last: break
                meta = [(b['type'], b['data']) for b in src_blocks if b['type'] not in (0, 3, 0x7F)]
                # Build output
                result = bytearray(b'fLaC')
                result.append(0x00); result += struct.pack('>I', len(tmp_si))[1:4]; result += tmp_si
                for ti, (bt, bd) in enumerate(meta):
                    is_l = ti == len(meta)-1
                    result.append((bt & 0x7F) | (0x80 if is_l else 0))
                    result += struct.pack('>I', len(bd))[1:4]; result += bd
                if not meta: result[4] = 0x80
                result += tmp_aud
                out_p = os.path.splitext(fp)[0] + f'_compressed_{target_sr}Hz.flac'
                with open(out_p, 'wb') as f: f.write(result)
                after_mb = len(result)/1024/1024
                total_before += size_mb; total_after += after_mb
                t4_log(f"  {size_mb:.0f}MB → {after_mb:.0f}MB (节省{size_mb-after_mb:.0f}MB)")
            except Exception as e:
                t4_log(f"  错误: {e}")
            t4_bar['value'] = idx + 1
        saved = total_before - total_after
        t4_log(f"\n完成! 节省 {saved:.0f}MB ({saved/total_before*100:.0f}%)" if total_before else "完成!")

    def t4_poll():
        try:
            while True:
                msg = t4_log_queue.get_nowait()
                t4_out.insert('end', str(msg) + '\n'); t4_out.see('end')
        except queue.Empty: pass
        root.after(100, t4_poll)
    t4_poll()

    # ═══════════════════════════════════════
    #  Tab 5: 高级检测 (batch_fix 集成)
    # ═══════════════════════════════════════
    tab5 = ttk.Frame(nb)
    nb.add(tab5, text=" 高级检测 ")

    f5t = ttk.Frame(tab5, padding=5)
    f5t.pack(fill='x')
    d5_var = tk.StringVar(value=os.getcwd())
    ttk.Label(f5t, text="目录:").pack(side='left')
    ttk.Entry(f5t, textvariable=d5_var, width=40).pack(side='left', padx=5, fill='x', expand=True)
    ttk.Button(f5t, text="浏览...", command=lambda: d5_var.set(filedialog.askdirectory())).pack(side='left', padx=2)
    ttk.Button(f5t, text="深度扫描", command=lambda: threading.Thread(target=t5_scan, daemon=True).start()).pack(side='left', padx=2)
    ttk.Button(f5t, text="修复PICTURE", command=lambda: threading.Thread(target=t5_fix, daemon=True).start()).pack(side='left', padx=2)
    ttk.Button(f5t, text="截取修复", command=lambda: threading.Thread(target=t5_truncate, daemon=True).start()).pack(side='left', padx=2)
    ttk.Button(f5t, text="批量修复损坏", command=lambda: threading.Thread(target=t5_batch_truncate, daemon=True).start()).pack(side='left', padx=2)

    f5stat = tk.StringVar(value="")
    ttk.Label(tab5, textvariable=f5stat, padding=3).pack(fill='x')

    f5tv = ttk.Frame(tab5)
    f5tv.pack(fill='both', expand=True, padx=5)
    t5_tree = ttk.Treeview(f5tv, columns=('filename', 'fmt', 'duration', 'sr', 'bps', 'cover', 'verdict'),
                           show='headings', height=14)
    t5_tree.heading('filename', text='文件名'); t5_tree.column('filename', width=200)
    t5_tree.heading('fmt', text='格式'); t5_tree.column('fmt', width=50, anchor='center')
    t5_tree.heading('duration', text='时长'); t5_tree.column('duration', width=60, anchor='center')
    t5_tree.heading('sr', text='采样率'); t5_tree.column('sr', width=70, anchor='center')
    t5_tree.heading('bps', text='位深'); t5_tree.column('bps', width=50, anchor='center')
    t5_tree.heading('cover', text='封面'); t5_tree.column('cover', width=120)
    t5_tree.heading('verdict', text='判定'); t5_tree.column('verdict', width=60, anchor='center')
    t5_tree.pack(side='left', fill='both', expand=True)
    t5_sb = ttk.Scrollbar(f5tv, orient='vertical', command=t5_tree.yview)
    t5_tree.configure(yscrollcommand=t5_sb.set); t5_sb.pack(side='right', fill='y')

    f5l = ttk.LabelFrame(tab5, text="日志", padding=3)
    f5l.pack(fill='both', padx=5, pady=3)
    t5_out = scrolledtext.ScrolledText(f5l, wrap='word', height=5, font=('Consolas', 9))
    t5_out.pack(fill='both', expand=True)

    t5_log_queue = queue.Queue(); t5_results = []

    def t5_log(msg):
        t5_log_queue.put(str(msg))

    def _scan_flac_adv(fp):
        try: import struct; import soundfile as sf
        except: return None
        r = {'path': fp, 'name': os.path.basename(fp), 'format': '.flac', 'sample_rate': 0,
             'channels': 0, 'bps': 0, 'duration': 0, 'size_mb': os.path.getsize(fp)/1024/1024,
             'has_cover': False, 'cover_mime': '', 'cover_size': 0, 'cover_info': '',
             'cover_needs_fix': False, 'verdict': 'ok', 'reason': '', 'decode_ok': True,
             'good_seconds': 0, 'error_offset': 0}
        try: r['bps'] = _get_flac_bps(fp)
        except: r['verdict'] = 'error'; r['reason'] = '不是有效FLAC'; return r
        try:
            info = sf.info(fp)
            r['sample_rate'] = info.samplerate; r['channels'] = info.channels; r['duration'] = info.duration
            r['format'] = '.flac'
        except Exception as e: r['verdict'] = 'error'; r['reason'] = str(e); return r
        # 解码完整性测试: 在 25% / 50% / 75% / 95% 位置尝试验证
        try:
            import soundfile as sf
            sr = info.samplerate; fr = info.frames
            for pct in [0.25, 0.5, 0.75, 0.95]:
                pos_frame = int(fr * pct)
                try: sf.read(fp, start=pos_frame, frames=int(sr*0.3), dtype='float32', always_2d=True)
                except:
                    r['decode_ok'] = False; r['verdict'] = 'corrupt'
                    # 二分查找精确损坏位置
                    lo, hi = 0, fr
                    while hi - lo > sr // 2:
                        mid = (lo + hi) // 2
                        try:
                            sf.read(fp, start=mid, frames=int(sr*0.2), dtype='float32', always_2d=True); lo = mid
                        except: hi = mid
                    r['error_offset'] = hi / sr
                    r['good_seconds'] = r['error_offset']
                    r['reason'] = f'解码失败@ {r["error_offset"]:.0f}s'
                    break
        except: pass
        # PICTURE
        with open(fp, 'rb') as f: raw = f.read(3*1024*1024)
        pos = 4
        while pos < len(raw):
            hdr = raw[pos:pos+4]; is_last = hdr[0] & 0x80; bt = hdr[0] & 0x7F
            bl = struct.unpack('>I', b'\x00'+hdr[1:4])[0]
            if bt == 6:
                r['has_cover'] = True
                pic = raw[pos+4:pos+4+bl]
                ml = struct.unpack('>I', pic[4:8])[0]
                r['cover_mime'] = pic[8:8+ml].decode('ascii','replace') if ml>0 else '(empty)'
                do = 8+ml; dl = struct.unpack('>I', pic[do:do+4])[0]
                io = do+4+dl
                pw=struct.unpack('>I', pic[io:io+4])[0]; ph=struct.unpack('>I', pic[io+4:io+8])[0]
                pd=struct.unpack('>I', pic[io+16:io+20])[0]
                issues = []
                if ml==0: issues.append('MIME缺失')
                if pw==0 or ph==0: issues.append(f'尺寸={pw}x{ph}')
                if issues: r['cover_needs_fix'] = True; r['cover_info'] = ', '.join(issues)
                else: r['cover_info'] = f'{pw}x{ph} {pd/1024:.0f}KB'
                r['cover_size'] = pd
            pos += 4+bl
            if is_last: break
        if r['cover_needs_fix']: r['verdict'] = 'pic_fix'; r['reason'] = 'PICTURE: '+r['cover_info']
        return r

    def t5_scan():
        nonlocal t5_results
        d = d5_var.get().strip()
        if not os.path.isdir(d): return
        files = [f for f in collect_audio_files(d) if f.lower().endswith('.flac')]
        t5_results = []
        t5_tree.delete(*t5_tree.get_children())
        t5_log(f"扫描 {len(files)} 个FLAC...")
        for i, fp in enumerate(files):
            r = _scan_flac_adv(fp)
            if r: t5_results.append(r)
            if r and i % 20 == 0: t5_log(f"  {i+1}/{len(files)}")
        # Populate tree
        for r in t5_results:
            dur = f"{r['duration']:.0f}s" if r['duration'] else '-'
            sr = f"{r['sample_rate']}Hz" if r['sample_rate'] else '-'
            cover = r.get('cover_info', '-') or '-'
            v_raw = r.get('verdict','')
            verdict = {'ok': '正常', 'pic_fix': '需修复', 'error': '错误', 'corrupt': '损坏'}.get(v_raw, v_raw)
            t5_tree.insert('', 'end', values=(r['name'][:50], r['format'], dur, sr,
                           f"{r['bps']}bit" if r['bps'] else '-', cover, verdict))
        ok = sum(1 for r in t5_results if r.get('verdict')=='ok')
        fix = sum(1 for r in t5_results if r.get('verdict')=='pic_fix')
        err = sum(1 for r in t5_results if r.get('verdict')=='error')
        cor = sum(1 for r in t5_results if r.get('verdict')=='corrupt')
        f5stat.set(f"共 {len(t5_results)} 个 | 正常:{ok} | PICTURE修复:{fix} | 损坏:{cor} | 错误:{err}")
        t5_log(f"扫描完成: 正常{ok}, 需修复{fix}, 错误{err}")

    def t5_fix():
        to_fix = [r for r in t5_results if r.get('verdict')=='pic_fix']
        if not to_fix: t5_log("没有需要修复的文件"); return
        fixed = 0
        for r in to_fix:
            fp = r['path']
            try:
                with open(fp, 'rb') as f: raw = bytearray(f.read())
                if raw[:4] != b'fLaC': continue
                blocks = []; pos = 4
                while pos < len(raw):
                    hdr = raw[pos:pos+4]; is_last = hdr[0] & 0x80; bt = hdr[0] & 0x7F
                    bl = struct.unpack('>I', b'\x00'+hdr[1:4])[0]
                    blocks.append({'type': bt, 'offset': pos, 'length': bl, 'is_last': bool(is_last)})
                    pos += 4+bl
                    if is_last: break
                for b in blocks:
                    if b['type'] == 6:
                        pd = raw[b['offset']+4:b['offset']+4+b['length']]
                        pd = _fix_pic_block(pd); b['length'] = len(pd); b['_data'] = pd
                if not any('_data' in b for b in blocks): continue
                result = bytearray(b'fLaC')
                for i, b in enumerate(blocks):
                    bt = b['type'] | (0x80 if i==len(blocks)-1 else 0)
                    d = b.pop('_data', raw[b['offset']+4:b['offset']+4+b['length']])
                    result.append(bt); result += struct.pack('>I', b['length'])[1:4]; result += d
                result += raw[pos:]
                with open(fp, 'wb') as f: f.write(result)
                fixed += 1; t5_log(f"  修复: {os.path.basename(fp)[:45]}")
            except Exception as e: t5_log(f"  失败: {os.path.basename(fp)[:45]} - {e}")
        t5_log(f"修复完成: {fixed}/{len(to_fix)}")

    def _fix_pic_block(pd):
        pt = struct.unpack('>I', pd[:4])[0]; ml = struct.unpack('>I', pd[4:8])[0]
        m = pd[8:8+ml] if ml>0 else b""; do = 8+ml; dl = struct.unpack('>I', pd[do:do+4])[0]
        io = do+4+dl
        w=struct.unpack('>I', pd[io:io+4])[0]; h=struct.unpack('>I', pd[io+4:io+8])[0]
        cd=struct.unpack('>I', pd[io+8:io+12])[0]; nc=struct.unpack('>I', pd[io+12:io+16])[0]
        ps=struct.unpack('>I', pd[io+16:io+20])[0]; img=pd[io+20:io+20+ps]
        if ml>0 and w>0 and h>0: return pd
        if ml==0:
            if img[:2]==b'\xff\xd8': m=b'image/jpeg'
            elif img[:4]==b'\x89PNG': m=b'image/png'
            else: m=b'image/jpeg'
        if w==0 or h==0:
            try:
                p2=0
                while p2<len(img)-1:
                    if img[p2]!=0xFF: break
                    mk=img[p2+1]; p2+=2
                    if mk in (0xC0, 0xC1, 0xC2):
                        if p2+7<=len(img): h=struct.unpack('>H', img[p2+3:p2+5])[0]; w=struct.unpack('>H', img[p2+5:p2+7])[0]
                        break
                    if p2+2>len(img): break
                    p2+=struct.unpack('>H', img[p2:p2+2])[0]
            except: pass
        if w==0: w=1000
        if h==0: h=1000
        buf=bytearray(); buf+=struct.pack('>I',pt); buf+=struct.pack('>I',len(m)); buf+=m
        buf+=struct.pack('>I',dl); buf+=pd[do+4:do+4+dl]
        buf+=struct.pack('>I',w); buf+=struct.pack('>I',h)
        buf+=struct.pack('>I',cd); buf+=struct.pack('>I',nc); buf+=struct.pack('>I',ps); buf+=img
        return bytes(buf)

    def _truncate_one(fp, r, out_suffix='_repaired'):
        """读取 FLAC 的有效部分并输出新文件, 返回 (output_path, before_mb, after_mb) 或 None"""
        import soundfile as sf
        try: import numpy as np
        except: pass
        sr = r['sample_rate']
        # 分块读取直到失败
        chunks = []; offset = 0; chunk_f = int(sr * 10)
        while True:
            count = min(chunk_f, int(sr * 3600))  # max 1 hour
            try:
                d, _ = sf.read(fp, start=offset, frames=count, dtype='float32', always_2d=True)
                if d.shape[0] > 0: chunks.append(d); offset += d.shape[0]
                if d.shape[0] < count: break  # read fewer than requested → EOF or error next
            except Exception:
                break
        if not chunks: return None
        audio = np.concatenate(chunks, axis=0) if len(chunks) > 1 else chunks[0]
        out_p = os.path.splitext(fp)[0] + f'{out_suffix}.flac'
        sf.write(out_p, audio, sr, subtype='PCM_16')
        in_mb = os.path.getsize(fp)/1024/1024
        out_mb = os.path.getsize(out_p)/1024/1024
        return (out_p, in_mb, out_mb, audio.shape[0] / sr)

    def t5_truncate():
        corrupt = [r for r in t5_results if r.get('verdict')=='corrupt']
        if not corrupt: t5_log("没有需要修复的损坏FLAC文件"); return
        sel = t5_tree.selection()
        if sel: corrupt = [t5_results[t5_tree.index(s)] for s in sel if t5_tree.index(s) < len(t5_results) and t5_results[t5_tree.index(s)].get('verdict')=='corrupt']
        if not corrupt: t5_log("请先选中要修复的行再点击截取修复"); return
        for r in corrupt:
            t5_log(f"截取修复: {os.path.basename(r['path'])[:50]}")
            res = _truncate_one(r['path'], r)
            if res:
                t5_log(f"  {res[1]:.0f}MB → {res[2]:.0f}MB ({res[3]:.0f}s)")
            else:
                t5_log(f"  失败: 无法读取有效音频")

    def t5_batch_truncate():
        corrupt = [r for r in t5_results if r.get('verdict')=='corrupt']
        if not corrupt: t5_log("未检测到损坏文件，请先运行深度扫描"); return
        t5_log(f"\n{'='*50}\n批量截取修复: {len(corrupt)} 个文件\n{'='*50}")
        fixed = 0
        for idx, r in enumerate(corrupt):
            fn = os.path.basename(r['path'])
            t5_log(f"[{idx+1}/{len(corrupt)}] {fn[:50]}")
            res = _truncate_one(r['path'], r)
            if res:
                t5_log(f"  → {os.path.basename(res[0])} ({res[1]:.0f}MB → {res[2]:.0f}MB, {res[3]:.0f}s)")
                fixed += 1
            else:
                t5_log(f"  失败")
        t5_log(f"\n修复完成: {fixed}/{len(corrupt)}")

    def t5_poll():
        try:
            while True:
                msg = t5_log_queue.get_nowait()
                t5_out.insert('end', str(msg) + '\n'); t5_out.see('end')
        except queue.Empty: pass
        root.after(100, t5_poll)
    t5_poll()

    # ═══════════════════════════════════════
    #  Tab 6: 合辑标记 (COMPILATION=1)
    # ═══════════════════════════════════════
    tab6 = ttk.Frame(nb)
    nb.add(tab6, text=" 合辑标记 ")

    f6t = ttk.Frame(tab6, padding=5)
    f6t.pack(fill='x')
    d6_var = tk.StringVar(value=os.getcwd())
    ttk.Label(f6t, text="目录:").pack(side='left')
    ttk.Entry(f6t, textvariable=d6_var, width=40).pack(side='left', padx=5, fill='x', expand=True)
    ttk.Button(f6t, text="浏览...", command=lambda: d6_var.set(filedialog.askdirectory())).pack(side='left', padx=2)
    ttk.Button(f6t, text="扫描合辑", command=lambda: threading.Thread(target=t6_scan, daemon=True).start()).pack(side='left', padx=2)
    ttk.Button(f6t, text="标记全部", command=lambda: threading.Thread(target=t6_apply_all, daemon=True).start()).pack(side='left', padx=2)

    f6t2 = ttk.Frame(tab6, padding=3)
    f6t2.pack(fill='x')
    ttk.Label(f6t2, text="Album Artist:").pack(side='left')
    t6_aa_var = tk.StringVar(value="Various Artists")
    ttk.Entry(f6t2, textvariable=t6_aa_var, width=25).pack(side='left', padx=3)
    ttk.Button(f6t2, text="应用专辑艺术家(全部)", command=lambda: threading.Thread(target=t6_apply_artist_all, daemon=True).start()).pack(side='left', padx=2)
    ttk.Button(f6t2, text="应用专辑艺术家(选中)", command=lambda: threading.Thread(target=t6_apply_artist_sel, daemon=True).start()).pack(side='left', padx=2)

    f6stat = tk.StringVar(value="")
    ttk.Label(tab6, textvariable=f6stat, padding=3).pack(fill='x')

    # 双栏: 左侧合辑列表, 右侧文件详情
    f6pan = ttk.PanedWindow(tab6, orient='horizontal')
    f6pan.pack(fill='both', expand=True, padx=5, pady=3)

    f6left = ttk.Frame(f6pan)
    f6left.grid_rowconfigure(0, weight=1); f6left.grid_columnconfigure(0, weight=1)
    t6_tree = ttk.Treeview(f6left, columns=('album', 'path', 'tagged', 'need'),
                           show='headings', height=16)
    t6_tree.heading('album', text='专辑'); t6_tree.column('album', width=160)
    t6_tree.heading('path', text='路径'); t6_tree.column('path', width=180)
    t6_tree.heading('tagged', text='已标记'); t6_tree.column('tagged', width=55, anchor='center')
    t6_tree.heading('need', text='待标记'); t6_tree.column('need', width=55, anchor='center')
    t6_tree.grid(row=0, column=0, sticky='nsew')
    t6_sb = ttk.Scrollbar(f6left, orient='vertical', command=t6_tree.yview)
    t6_tree.configure(yscrollcommand=t6_sb.set); t6_sb.grid(row=0, column=1, sticky='ns')
    f6pan.add(f6left, weight=1)

    f6right = ttk.Frame(f6pan)
    f6right.grid_rowconfigure(0, weight=1); f6right.grid_columnconfigure(0, weight=1)
    t6_det = ttk.Treeview(f6right, columns=('file', 'artist', 'status'), show='headings', height=16)
    t6_det.heading('file', text='文件'); t6_det.column('file', width=180)
    t6_det.heading('artist', text='歌手'); t6_det.column('artist', width=120)
    t6_det.heading('status', text='标签'); t6_det.column('status', width=60, anchor='center')
    t6_det.grid(row=0, column=0, sticky='nsew')
    det_sb = ttk.Scrollbar(f6right, orient='vertical', command=t6_det.yview)
    t6_det.configure(yscrollcommand=det_sb.set); det_sb.grid(row=0, column=1, sticky='ns')
    f6pan.add(f6right, weight=1)

    ttk.Button(f6right, text="标记此专辑", command=lambda: threading.Thread(target=t6_apply_sel, daemon=True).start()).grid(row=1, column=0, columnspan=2, pady=3)

    f6l = ttk.LabelFrame(tab6, text="日志", padding=3)
    f6l.pack(fill='both', padx=5, pady=3)
    t6_out = scrolledtext.ScrolledText(f6l, wrap='word', height=4, font=('Consolas', 9))
    t6_out.pack(fill='both', expand=True)

    t6_results = []; t6_log_queue = queue.Queue()

    def t6_log(msg):
        t6_log_queue.put(str(msg))

    def t6_scan():
        nonlocal t6_results
        d = d6_var.get().strip()
        if not os.path.isdir(d): return
        t6_results = find_compilations(d, t6_log)
        t6_tree.delete(*t6_tree.get_children()); t6_det.delete(*t6_det.get_children())
        for i, r in enumerate(t6_results):
            t6_tree.insert('', 'end', iid=f'C{i}',
                           values=(r['album'][:40], os.path.basename(r['dir'])[:30],
                                   str(r.get('tagged_count', 0)), str(r['need_tag_count'])))
        # 绑定点选
        def on_sel(event):
            sel = t6_tree.selection()
            t6_det.delete(*t6_det.get_children())
            if sel:
                idx = int(sel[0][1:])
                if idx < len(t6_results):
                    for f in t6_results[idx]['files']:
                        st = '已标记' if f['compilation'] else '待标记'
                        t6_det.insert('', 'end', values=(f['name'][:45], f['artist'][:25], st))
        t6_tree.bind('<<TreeviewSelect>>', on_sel)
        total_need = sum(r['need_tag_count'] for r in t6_results)
        f6stat.set(f"发现 {len(t6_results)} 张合辑, 共 {total_need} 个文件待标记")

    def t6_apply_all():
        if not t6_results: t6_log("请先扫描"); return
        total = 0
        for i, r in enumerate(t6_results):
            t6_log(f"合辑 [{r['album'][:30]}]")
            n = apply_compilation_tags(r, t6_log)
            total += n
            t6_tree.set(f'C{i}', 'tagged', str(r.get('tagged_count', 0)))
            t6_tree.set(f'C{i}', 'need', str(r['need_tag_count']))
        t6_log(f"\n全部完成: 标记了 {total} 个文件")

    def t6_apply_sel():
        sel = t6_tree.selection()
        if not sel: t6_log("请先在左侧选择一张合辑"); return
        idx = int(sel[0][1:])
        if idx >= len(t6_results): return
        r = t6_results[idx]
        t6_log(f"合辑 [{r['album'][:30]}]")
        apply_compilation_tags(r, t6_log)
        t6_tree.set(f'C{idx}', 'tagged', str(r.get('tagged_count', 0)))
        t6_tree.set(f'C{idx}', 'need', str(r['need_tag_count']))
        # 刷新详情
        t6_det.delete(*t6_det.get_children())
        for f in r['files']:
            st = '已标记' if f['compilation'] else '待标记'
            t6_det.insert('', 'end', values=(f['name'][:45], f['artist'][:25], st))

    def t6_apply_artist_all():
        if not t6_results: t6_log("请先扫描"); return
        aa = t6_aa_var.get().strip()
        if not aa: t6_log("请输入 Album Artist"); return
        total = 0
        for r in t6_results:
            t6_log(f"合辑 [{r['album'][:30]}]")
            total += apply_album_artist(r, aa, t6_log)
        t6_log(f"\n完成: 设置了 {total} 个文件的 ALBUMARTIST={aa}")

    def t6_apply_artist_sel():
        sel = t6_tree.selection()
        if not sel: t6_log("请先在左侧选择一张合辑"); return
        aa = t6_aa_var.get().strip()
        if not aa: t6_log("请输入 Album Artist"); return
        idx = int(sel[0][1:])
        if idx >= len(t6_results): return
        r = t6_results[idx]
        t6_log(f"合辑 [{r['album'][:30]}]")
        apply_album_artist(r, aa, t6_log)

    def t6_poll():
        try:
            while True:
                msg = t6_log_queue.get_nowait()
                t6_out.insert('end', str(msg) + '\n'); t6_out.see('end')
        except queue.Empty: pass
        root.after(100, t6_poll)
    t6_poll()

    root.mainloop()


# ═══════════════════════════════════════════
#  交互控制台菜单
# ═══════════════════════════════════════════

def run_menu():
    """无参数时的交互菜单"""
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"""
  ╔══════════════════════════════════════╗
  ║     MusicTools v{VERSION}              ║
  ║     音乐文件工具箱                    ║
  ╚══════════════════════════════════════╝

  [1] tag     — 从文件名写入标签
  [2] fix     — 修复 FLAC PICTURE 封面
  [3] check   — 检测缺失信息
  [4] lyrics  — 搜索歌词 (QQ+网易云)
  [g] GUI     — 启动图形界面
  [q] 退出
""")
    while True:
        try:
            ch = input("  选择 [1-4/g/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  退出"); break
        if ch == 'q':
            print("  再见!"); break
        if ch == 'g':
            run_gui(); break
        if ch not in ('1', '2', '3', '4'):
            continue
        try:
            d = input("  输入目录路径: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  退出"); break
        if not d: d = '.'
        if not os.path.isdir(d):
            print(f"  目录不存在: {d}")
            continue
        print(f"\n{'='*50}")
        if ch == '1': cmd_tag(d)
        elif ch == '2': cmd_fix(d)
        elif ch == '3': cmd_check(d)
        elif ch == '4':
            print("  歌词搜索需要键盘交互，已开始...")
            cmd_lyrics_interactive(d)
        print(f"\n{'='*50}\n")
        input("  按回车返回菜单...")
        os.system('cls' if os.name == 'nt' else 'clear')
        return run_menu()  # 重新显示菜单


# ═══════════════════════════════════════════
#  歌词交互搜索
# ═══════════════════════════════════════════

# ═══════════════════════════════════════════
#  本地 AI 工具
# ═══════════════════════════════════════════

def check_lrcmaker():
    """检测 LRCMaker-AI-Backend 是否运行 (8000端口已监听即视为运行中)"""
    from urllib.error import HTTPError
    try:
        req = Request('http://127.0.0.1:8000/', headers={'Connection': 'close'})
        resp = urlopen(req, timeout=2)
        resp.close()
        return True
    except HTTPError:
        return True  # 404也算运行中 (LRCMaker 没有根路由)
    except Exception:
        return False


def check_zimu():
    """检测 zimu-agent 是否运行 (5003端口 /api/ping)"""
    try:
        req = Request('http://127.0.0.1:5003/api/ping',
                      headers={'Connection': 'close'})
        resp = urlopen(req, timeout=3)
        data = json.loads(resp.read())
        resp.close()
        return data.get('ready', False)
    except Exception:
        return False


def ai_align_with_lrcmaker(audio_path, lyrics_text, title="", artist="", album=""):
    """调用 LRCMaker AI 将文本与音频对齐生成 LRC"""
    boundary = '----MusicToolsBoundary'
    body = bytearray(b'')
    for name, value in [('lyrics', lyrics_text), ('ti', title), ('ar', artist), ('al', album)]:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8')
    fn = os.path.basename(audio_path)
    with open(audio_path, 'rb') as f: audio_bytes = f.read()
    body += f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="{fn}"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
    body += audio_bytes
    body += f'\r\n--{boundary}--\r\n'.encode()
    try:
        req = Request('http://127.0.0.1:8000/api/align', data=body,
                      headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
        resp = urlopen(req, timeout=300)
        data = json.loads(resp.read())
        if data.get('code') == 200: return data.get('data', '')
    except Exception as e:
        print(f"  [LRCMaker错误] {e}")
    return None


# ═══════════════════════════════════════════
#  zimu-agent 集成 — 完全复制网页前端 app.js 处理流程
# ═══════════════════════════════════════════

PUNCT_SET = set("，。！？；：,.!?;:")
_PUNCT_RE = re.compile(r'[，。！？；：,.!?;:]')


def _z_parse_time(value):
    """复刻 parseTimeToSeconds"""
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip()
    if not text: return None
    if ":" in text:
        try:
            parts = [float(p) for p in text.split(":")]
        except ValueError: return None
        if any(math.isnan(n) for n in parts): return None
        if len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2: return parts[0] * 60 + parts[1]
        return None
    try:
        n = float(text)
        return n if math.isfinite(n) else None
    except ValueError: return None


def _z_format_lrc(seconds):
    """复刻 formatLrcTime — mm:ss.xx"""
    if not isinstance(seconds, (int, float)) or math.isnan(seconds):
        return "00:00.00"
    cs = max(0, math.floor(seconds * 100))
    return f"{cs // 6000:02d}:{(cs % 6000) // 100:02d}.{cs % 100:02d}"


def _z_to_comma(text):
    """复刻 toCommaOnlyForSrt"""
    t = text.replace("\r", "\n").replace("\n", "")
    t = _PUNCT_RE.sub('，', t)
    t = re.sub(r'，{2,}', '，', t)
    return re.sub(r'^，|，$', '', t).strip()


def _z_norm_text(text, preserve=False):
    """复刻 normalizeSubtitleText"""
    raw = text.replace("\r", "\n").replace("\n", "").strip()
    if not preserve: return _z_to_comma(raw)
    return re.sub(r'\s+', ' ', raw).strip()


def _z_plain_len(text):
    """复刻 plainLen"""
    return len(re.sub(r'[\s，。！？；：,.!?;:]+', '', str(text or '')))


def _z_strip_repair(text):
    """复刻 stripSpeechRepairText"""
    s = str(text or '').replace("\r", "\n").replace("\n", "")
    return list(re.sub(r'[\s，。！？；：,.!?;:]+', '', s))


def _z_split_tail(text):
    """复刻 splitCueTailPunctuation"""
    cleaned = str(text or '').replace("\r", "\n").replace("\n", "").strip()
    m = re.match(r'^(.*?)([，。！？；：,.!?;:]+)?$', cleaned)
    if m: return {'body': m.group(1) or '', 'tail': m.group(2) or ''}
    return {'body': cleaned, 'tail': ''}


# ── 原子段合并 ──

def _z_merge_atomic(seg_list):
    """复刻 mergeAtomicSegments"""
    if not seg_list: return []
    avg_len = sum(len(str(s.get('text', '')).strip()) for s in seg_list) / len(seg_list)
    tiny_ratio = sum(1 for s in seg_list if len(str(s.get('text', '')).strip()) <= 2) / max(len(seg_list), 1)
    if avg_len > 2.2 and tiny_ratio < 0.65: return seg_list

    merged, cur = [], None
    def flush():
        nonlocal cur
        if cur is None: return
        txt = str(cur['text']).strip()
        if txt and cur['end_time'] > cur['start_time']: cur['text'] = txt; merged.append(dict(cur))
        cur = None

    for seg in seg_list:
        text = str(seg.get('text', '')).strip()
        if not text: continue
        s, e = float(seg.get('start_time', 0)), float(seg.get('end_time', 0))
        if not (math.isfinite(s) and math.isfinite(e) and e > s): continue
        if cur is None: cur = {'start_time': s, 'end_time': e, 'text': text}; continue
        gap = s - cur['end_time']; ct = str(cur.get('text', '')); pl = _z_plain_len(ct)
        cd = cur['end_time'] - cur['start_time']
        if gap > 0.45 or (ct and ct[-1] in PUNCT_SET and pl >= 6) or pl >= 26 or cd >= 3.8:
            flush(); cur = {'start_time': s, 'end_time': e, 'text': text}
        else:
            cur['end_time'] = max(cur['end_time'], e); cur['text'] = ct + text
    flush()
    return merged if merged else seg_list


# ── 根据停顿注入标点 ──

def _z_inject_pause(seg_list):
    """复刻 injectPausePunctuation"""
    if not seg_list: return []
    cp, pp, mn, bk = 0.28, 0.58, 6, 18
    def has_tail(t): return bool(re.search(r'[，。！？；：,.!?;:]$', str(t or '').strip()))
    def is_q(t):
        t = re.sub(r'[，。！？；：,.!?;:]+$', '', str(t or '').strip())
        if not t: return False
        if re.search(r'[吗呢么]$', t): return True
        return any(p in t for p in ["是不是", "是否", "怎么", "怎样", "如何", "为什么", "为何", "多少", "哪里", "哪儿", "哪个", "谁"])
    def ensure(t, ch):
        t = str(t or '').strip()
        if not t or has_tail(t): return t
        c = "？" if is_q(t) else ch
        return t + c if c else t

    out = []
    for i, seg in enumerate(seg_list):
        s, e = float(seg.get('start_time', 0)), float(seg.get('end_time', 0))
        text = str(seg.get('text', '')).strip()
        if not (math.isfinite(s) and math.isfinite(e) and e > s and text): continue
        last = i == len(seg_list) - 1
        ns = float(seg_list[i + 1].get('start_time', 0)) if not last else None
        gap = max(0, ns - e) if ns is not None else 0; cl = _z_plain_len(text)
        p = ""
        if not has_tail(text):
            if last or gap >= pp: p = "。"
            elif (gap >= cp and cl >= mn) or cl >= bk: p = "，"
        out.append({'start_time': s, 'end_time': e, 'text': ensure(text, p)})
    return out if out else seg_list


# ── 段标准化 ──

def _z_normalize_segments(segments):
    """复刻 normalizeSegmentsForSrt"""
    if not isinstance(segments, list): return []
    seg_list = []
    for seg in segments:
        s = _z_parse_time(seg.get('start_time')); e = _z_parse_time(seg.get('end_time'))
        t = str(seg.get('text', '')).replace("\r", "\n").replace("\n", "").strip()
        if s is None or e is None or e <= s or not t: continue
        seg_list.append({'start_time': s, 'end_time': e, 'text': t})
    seg_list.sort(key=lambda x: (x['start_time'], x['end_time']))
    puncted = _z_inject_pause(_z_merge_atomic(seg_list))
    normed = []
    for seg in puncted:
        s, e = seg['start_time'], seg['end_time']
        if normed and s < normed[-1]['end_time']: s = normed[-1]['end_time']
        if e - s < 0.05: continue
        normed.append({'start_time': s, 'end_time': e, 'text': str(seg.get('text', '')).replace("\r", "\n").replace("\n", "").strip()})
    return normed


# ── 短句分割 ──

def _z_split_maxlen(text, max_len=14):
    """复刻 splitByMaxLen"""
    limit = max(6, max_len); rest = str(text or '').strip(); out = []
    while len(rest) > limit:
        probe = rest[:limit + 1]; cut = -1
        for m in _PUNCT_RE.finditer(probe): cut = m.start() + 1
        if cut < math.floor(limit * 0.55):
            sp = probe.rfind(" ")
            if sp >= math.floor(limit * 0.55): cut = sp + 1
        if cut <= 0 or cut > len(probe): cut = limit
        out.append(rest[:cut].strip()); rest = rest[cut:].strip()
    if rest: out.append(rest)
    return [x for x in out if x]


def _z_compact_tiny(chunks):
    """复刻 compactTinyChunks"""
    if len(chunks) <= 1: return chunks
    out = []
    for c in chunks:
        cur = str(c or '').strip()
        if not cur: continue
        if not out: out.append(cur)
        elif _z_plain_len(cur) <= 2: out[-1] = out[-1] + cur
        else: out.append(cur)
    return out


def _z_split_short(text, max_len=14):
    """复刻 splitShortLines"""
    cleaned = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not cleaned: return []
    parts = re.findall(r'[^，。！？；：,.!?;:]+[，。！？；：,.!?;:]*', cleaned)
    if not parts: return _z_split_maxlen(cleaned, max(max_len, 22))
    out = []
    for part in parts:
        piece = part.strip()
        if not piece: continue
        if len(piece) > max(max_len * 1.8, 28):
            for x in _z_split_maxlen(piece, max(max_len, 22)): out.append(x)
        else: out.append(piece)
    return _z_compact_tiny([x for x in out if x])


# ── 构建短句列表 ──

def _z_build_short_cues(segments):
    """复刻 buildShortCueList"""
    normalized = _z_normalize_segments(segments)
    cues = []
    for seg in normalized:
        s = _z_parse_time(seg.get('start_time')); e = _z_parse_time(seg.get('end_time'))
        if s is None or e is None or e <= s: continue
        text = _z_to_comma(seg.get('text', ''))
        if not text: continue
        dur = e - s
        if dur <= 2.6 and len(text) <= 26: cues.append({'start': s, 'end': e, 'text': text}); continue
        max_l = 20 if dur > 5.0 else 24
        chunks = [_z_to_comma(c) for c in _z_split_short(text, max_l)]
        chunks = [c for c in chunks if c]
        if len(chunks) <= 1: cues.append({'start': s, 'end': e, 'text': text}); continue
        weights = []
        for c in chunks:
            w = len(re.sub(r'[，。！？；：,.!?;:]', '', c)); weights.append(w if w > 0 else 1)
        tw = sum(weights) or 1; durs = [dur * (w / tw) for w in weights]
        if dur > 0.4 * len(chunks):
            durs = [max(0.18, d) for d in durs]; sd = sum(durs) or dur; scale = dur / sd; durs = [d * scale for d in durs]
        cursor = s
        for idx, chunk in enumerate(chunks):
            last = idx == len(chunks) - 1; ds = durs[idx] or 0
            nc = e if last else min(e, cursor + ds)
            if nc > cursor: cues.append({'start': cursor, 'end': nc, 'text': chunk})
            cursor = nc
    return cues


# ── 构建字符 Token ──

def _z_build_tokens(cues):
    """复刻 buildSpeechCueTokens"""
    tokens = []
    for cue in (cues or []):
        s, e = float(cue.get('start', 0)), float(cue.get('end', 0))
        if not (math.isfinite(s) and math.isfinite(e) and e > s): continue
        body = _z_strip_repair(cue.get('text', ''))
        if not body: continue
        dur = max(0.001, e - s)
        # 确保每字至少 0.15s, 避免时间压缩 (ASR 短时戳被修复文本撑大)
        min_dur = len(body) * 0.15
        if dur < min_dur:
            e = s + min_dur; dur = min_dur
        for idx, ch in enumerate(body):
            cs = s + dur * (idx / len(body)); ce = e if idx == len(body) - 1 else s + dur * ((idx + 1) / len(body))
            tokens.append({'text': ch, 'start': cs, 'end': ce})
    return tokens


def _z_repair_cues(cues, full_text):
    """复刻 repairSpeechCueTexts"""
    if not cues: return {'cues': [], 'changed': False}
    truth = _z_strip_repair(full_text)
    if not truth: return {'cues': cues, 'changed': False}
    meta = []
    for cue in cues:
        p = _z_split_tail(cue.get('text', '')); b = _z_strip_repair(p['body'])
        meta.append({'cue': cue, 'body': b, 'tail': p['tail']})
    flat = []
    for ci, m in enumerate(meta):
        for ch in m['body']: flat.append({'ch': ch, 'cue_idx': ci})
    if not flat: return {'cues': cues, 'changed': False}
    rb = [[] for _ in meta]; orig = ''.join(f['ch'] for f in flat)
    la = 16; ti = ci = 0; lci = flat[0]['cue_idx']
    def nfm(t, si):
        lim = min(len(flat), si + la + 1)
        for i in range(si, lim):
            if flat[i]['ch'] == t: return i
        return -1
    def ntm(t, si):
        lim = min(len(truth), si + la + 1)
        for i in range(si, lim):
            if truth[i] == t: return i
        return -1
    while ti < len(truth) and ci < len(flat):
        tc = truth[ti]; cc = flat[ci]['ch']; cci = flat[ci]['cue_idx']
        if tc == cc: rb[cci].append(tc); lci = cci; ti += 1; ci += 1; continue
        ncm = nfm(tc, ci + 1); ntm_v = ntm(cc, ti + 1)
        if ntm_v != -1 and (ncm == -1 or (ntm_v - ti) <= (ncm - ci)):
            tgt = lci if isinstance(lci, int) else cci
            while ti < ntm_v: rb[tgt].append(truth[ti]); ti += 1
            continue
        if ncm != -1: ci += 1; continue
        rb[cci].append(tc); lci = cci; ti += 1; ci += 1
    aci = lci if isinstance(lci, int) else (len(meta)-1 if meta else 0)
    while ti < len(truth): rb[aci].append(truth[ti]); ti += 1
    repaired = []
    for idx, m in enumerate(meta):
        nc = dict(m['cue']); nc['text'] = ''.join(rb[idx]) + m['tail']; repaired.append(nc)
    rcomb = ''.join(''.join(b) for b in rb)
    return {'cues': repaired, 'changed': rcomb != orig}


def _z_regroup(cues, full_text):
    """复刻 regroupSpeechCuesByTruthPunctuation
    额外: 连续 50 字符无标点则强制断句 (避免一个 cue 包含整首歌)
    """
    if not cues: return {'cues': [], 'changed': False}
    raw = str(full_text or '').replace("\r", "\n").replace("\n", "").strip()
    if not raw: return {'cues': cues, 'changed': False}
    tokens = _z_build_tokens(cues)
    if not tokens: return {'cues': cues, 'changed': False}
    chars = list(raw); rebuilt = []; tix = 0
    ct, cs, ce = "", None, None
    def flush():
        nonlocal ct, cs, ce
        t = _z_norm_text(ct, True)
        if not t or cs is None or ce is None or ce <= cs: ct = ""; cs = None; ce = None; return
        rebuilt.append({'start': cs, 'end': ce, 'text': t}); ct = ""; cs = None; ce = None
    for ch in chars:
        if not ch: continue
        if ch.isspace():
            if ct: ct += ch
            continue
        if ch in PUNCT_SET:
            if ct: ct += ch; flush()
            continue
        if tix >= len(tokens): continue
        tk = tokens[tix]
        if cs is None: cs = tk['start']
        ce = tk['end']; ct += ch; tix += 1
        # 连续 50 字无标点 → 强制断句
        if len(ct) >= 50:
            flush()
    flush()
    if not rebuilt: return {'cues': cues, 'changed': False}
    o = '|'.join(_z_norm_text(c.get('text', ''), True) for c in cues)
    n = '|'.join(c['text'] for c in rebuilt)
    return {'cues': rebuilt, 'changed': o != n or len(rebuilt) != len(cues)}


def _z_build_lrc(cues):
    """复刻 buildLrcFromCues (preservePunctuation=True)"""
    if not cues: return ""
    lines = []
    for cue in cues:
        if not cue: continue
        s, e = cue.get('start', 0), cue.get('end', 0)
        if not (math.isfinite(s) and math.isfinite(e) and e > s): continue
        text = _z_norm_text(cue.get('text', ''), True)
        if not text: continue
        chars = list(text)
        if len(chars) <= 1: lines.append(f"[{_z_format_lrc(s)}]{text}"); continue
        dur = max(0.02, e - s)
        step = max(0.15, dur / len(chars))  # 每字最少 0.15s
        line = f"[{_z_format_lrc(s)}]"
        for idx, ch in enumerate(chars): line += f"<{_z_format_lrc(s + step * idx)}>{ch}"
        lines.append(line)
    return "\n".join(lines)


def _z_extract_text(data):
    """复刻 extractPlainText"""
    if isinstance(data.get('text'), str) and data['text'].strip():
        return data['text'].replace("\r", "\n").replace("\n", "").strip()
    if isinstance(data.get('segments'), list):
        parts = [str(s.get('text', '')).replace("\r", "\n").replace("\n", "").strip() for s in data['segments']]
        t = ''.join(p for p in parts)
        if t: return t
    raw = str(data.get('raw_text', '')).strip()
    if raw and (raw.startswith('[') or raw.startswith('{')):
        try:
            p = json.loads(raw)
            if isinstance(p, dict) and p.get('text'): return str(p['text']).strip()
        except: pass
    return raw


def zimu_transcribe(audio_path):
    """使用 curl 调用 zimu-agent /api/transcribe, 带后端存活监控
    返回完整 API 响应 dict, 失败返回 None
    """
    import tempfile, shutil, uuid

    file_size = os.path.getsize(audio_path)
    print(f"  [zimu] 音频 {os.path.basename(audio_path)} ({file_size/1024/1024:.1f}MB), 发送请求...")

    ext = os.path.splitext(audio_path)[1].lower()
    tmp_fp = os.path.join(tempfile.gettempdir(), f'zimu_{uuid.uuid4().hex[:8]}{ext}')
    try:
        shutil.copy2(audio_path, tmp_fp)
    except Exception as e:
        print(f"  [zimu错误] 无法复制临时文件: {e}")
        return None

    cmd = ['curl', '-s', '--connect-timeout', '10', '--max-time', '300',
           '-X', 'POST', 'http://127.0.0.1:5003/api/transcribe',
           '-F', f'file=@{tmp_fp}',
           '-F', 'model=qwen3', '-F', 'recognize_mode=speech',
           '-F', 'language=Chinese', '-F', 'max_new_tokens=256',
           '-F', 'align_srt=1', '-F', 'num_beams=1',
           '-F', 'temperature=0', '-F', 'top_p=1.0',
           '-F', 'repetition_penalty=1.0']

    si = None
    if sys.platform == 'win32':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    backend_dead = [False]  # 用列表实现跨线程标志

    def monitor_backend():
        """后台线程: 每 5 秒 ping zimu, 若失败则标记后端死亡"""
        import urllib.request
        while not backend_dead[0]:
            time.sleep(5)
            try:
                req = Request('http://127.0.0.1:5003/api/ping',
                              headers={'Connection': 'close'})
                resp = urlopen(req, timeout=3)
                resp.close()
            except Exception:
                backend_dead[0] = True
                return

    t0 = time.time()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace',
                                startupinfo=si)

        monitor = threading.Thread(target=monitor_backend, daemon=True)
        monitor.start()

        try:
            stdout, stderr = proc.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            print(f"  [zimu错误] 请求超时 (5分钟) — 后端可能卡死, 请重启 zimu-agent")
            return None

        backend_dead[0] = True  # 通知监控线程退出
        elapsed = time.time() - t0

        if proc.returncode != 0:
            if backend_dead[0]:
                print(f"  [zimu错误] zimu-agent 后端在处理过程中崩溃, 请重启 zimu-agent")
            else:
                print(f"  [zimu错误] curl 返回码 {proc.returncode}: {stderr[:300]}")
            return None

        if not stdout.strip():
            if backend_dead[0]:
                print(f"  [zimu错误] zimu-agent 后端崩溃, 无响应数据")
            else:
                print(f"  [zimu错误] 空响应")
            return None

        data = json.loads(stdout)
        if data.get('error'): print(f"  [zimu错误] {data['error']}"); return None
        if not data.get('ok'): print(f"  [zimu错误] API 返回失败"); return None
        print(f"  [zimu] 识别完成 ({elapsed:.0f}s)")
        return data

    except Exception as e:
        print(f"  [zimu错误] {e}")
        return None
    finally:
        try:
            if os.path.exists(tmp_fp): os.remove(tmp_fp)
        except Exception: pass


def zimu_build_lrc(data):
    """从 API 响应构建逐字 LRC — 复刻 computeTaskSrt speech 模式
    返回 LRC 文本, 失败返回 None
    """
    segments = data.get('segments', [])
    if not isinstance(segments, list) or not segments: return None
    full_text = _z_extract_text(data)
    # Step 1: buildShortCueList
    cues = _z_build_short_cues(segments)
    # Step 2: repairSpeechCueTexts
    rr = _z_repair_cues(cues, full_text); cues = rr['cues']
    # Step 3: regroupSpeechCuesByTruthPunctuation
    rg = _z_regroup(cues, full_text); cues = rg['cues']
    # Step 4: buildLrcFromCues
    lrc = _z_build_lrc(cues)
    return lrc


# ═══════════════════════════════════════════
#  歌词交互搜索 (含 AI 后备)
# ═══════════════════════════════════════════

def cmd_lyrics_interactive(root_dir):
    """命令行交互式歌词搜索 + AI 后备"""
    print(f"  lyrics: 歌词搜索")
    print(f"  目录: {root_dir}\n")

    lrcmaker_ok = check_lrcmaker()
    zimu_ok = check_zimu()
    if lrcmaker_ok: print(f"  [本地] LRCMaker   已就绪 (端口8000) — 输入文本 → AI对齐时间轴")
    if zimu_ok:     print(f"  [本地] zimu-agent 已就绪 (端口5003) — 语音识别自动生成")
    if not lrcmaker_ok and not zimu_ok:
        print(f"  [提示] 本地AI工具未启动, 只能在线搜索")
        print(f"         LRCMaker: 双击文件夹中的 LRCMaker_Backend_Win.exe")
        print(f"         zimu:     运行 zimu-agent/3.启动服务.bat")
    print()

    files = collect_audio_files(root_dir)
    print(f"  待处理: {len(files)} 个文件")
    found = online_found = 0; skipped_had = skipped_user = 0
    ai_lrcmaker = ai_zimu = 0

    for fp in sorted(files):
        fn = os.path.basename(fp)
        if has_lyrics(fp): skipped_had += 1; continue

        title, artist = resolve_song_metadata(fp)

        print(f"\n  {'─'*50}")
        print(f"  {fn[:50]}")
        print(f"  {title}  —  {artist or '(未知)'}")

        # ── 在线搜索结果 ──
        top = search_both(title, artist, 3)

        # ── 构建提示选项 ──
        option_parts = []
        if top: option_parts.append(f"1-{len(top)}=在线结果")
        opt_map = {}
        if lrcmaker_ok:
            option_parts.append("l=LRCMaker")
            opt_map['l'] = 'lrcmaker'
        if zimu_ok:
            option_parts.append("z=zimu")
            opt_map['z'] = 'zimu'
        option_parts.append("s=跳过")
        option_parts.append("q=退出")

        if top:
            for i, r in enumerate(top):
                stars = "★" * int(r['score'] * 5) + "☆" * max(0, 5 - int(r['score'] * 5))
                safe_print(f"  [{i+1}] {stars} [{r['src']}] {r['name'][:35]} — {r['artist'][:22]}")
        else:
            print("  [无在线搜索结果]")

        prompt = f"  选择 [{', '.join(option_parts)}]: "
        ch = input(prompt).strip().lower()

        if ch == 'q': break
        if ch == 's': skipped_user += 1; continue

        lrc = None

        # ── 在线选择 ──
        if ch.isdigit() and top:
            idx = int(ch)-1
            if 0 <= idx < len(top):
                lrc = fetch_lyric_by_result(top[idx])
                if lrc:
                    online_found += 1
                    print(f"  [在线] 下载成功 ({len(lrc)}字)")
                else:
                    print(f"  [无歌词] 该歌曲在平台上无歌词数据")

        # ── LRCMaker ──
        if not lrc and ch == 'l' and lrcmaker_ok:
            print(f"  [LRCMaker] 拖入歌词文件或粘贴文本 (空行结束):")
            first_line = input().strip('\u202a\u202c\u200e\u200f \t\n\r')  # 去除Windows复制路径时的隐藏Unicode字符
            # 判断是文件路径还是文本 (去引号支持拖入)
            clean_path = first_line.strip('"').strip("'")
            is_file = os.path.isfile(first_line) or os.path.isfile(clean_path)
            if is_file:
                actual_path = clean_path if os.path.isfile(clean_path) else first_line
                try:
                    with open(actual_path, 'r', encoding='utf-8') as f:
                        text = f.read().strip()
                except UnicodeDecodeError:
                    try:
                        with open(actual_path, 'r', encoding='gbk') as f:
                            text = f.read().strip()
                    except Exception:
                        with open(actual_path, 'r', encoding='utf-8', errors='ignore') as f:
                            text = f.read().strip()
                except Exception as e:
                    print(f"  [错误] 无法读取文件: {e}")
                    text = ''
                lines = text.split('\n') if text else []
                if lines:
                    print(f"  [读取] {len(lines)}行")
                else:
                    print(f"  [错误] 文件内容为空或无法读取")
            elif first_line:
                lines = [first_line]
                while True:
                    try: line = input()
                    except (EOFError, KeyboardInterrupt): break
                    if not line.strip(): break
                    lines.append(line.strip())
            else:
                lines = []
            if lines:
                text = '\n'.join(lines)
                print(f"  [LRCMaker] 正在对齐 ({len(lines)}行)...")
                lrc = ai_align_with_lrcmaker(fp, text, title, artist, os.path.basename(os.path.dirname(fp)))
                if lrc:
                    ai_lrcmaker += 1
                    print(f"  [LRCMaker] 对齐完成 ({len(lrc)}字)")
                else:
                    print(f"  [LRCMaker] 对齐失败, 请检查黑色窗口输出")
            else:
                print(f"  [取消] 未输入文本")

        # ── zimu (语音识别, 单次 API 调用 + 客户端 LRC 构建) ──
        if not lrc and ch == 'z' and zimu_ok:
            print(f"  [zimu] 正在进行语音识别...")
            data = zimu_transcribe(fp)
            if not data:
                print(f"  [zimu] 识别失败, 请查看 zimu-agent 窗口确认")
                continue

            # 客户端构建逐字 LRC (复刻网页前端 computeTaskSrt)
            print(f"  [zimu] 构建逐字 LRC...")
            lrc = zimu_build_lrc(data)
            if lrc:
                ai_zimu += 1
                print(f"  [zimu] 完成 ({len(lrc)} 字符, {lrc.count(chr(10)) + 1} 行)")
            else:
                print(f"  [zimu] LRC 构建失败, 无有效数据")

        # ── 聚合计数 ──
        if lrc and ch not in ('l', 'z'):
            found += 1  # 在线选择
        elif lrc and ch in ('l', 'z'):
            pass  # 已在上面计数

        # ── 保存 ──
        if lrc:
            embed_lyrics_to_file(fp, lrc, word_lrc=(ch == 'z'))
            print(f"  [保存] {os.path.splitext(fn)[0]}.lrc")
        elif ch not in ('l', 'z') and ch.isdigit():
            skipped_user += 1

    total_ai = ai_lrcmaker + ai_zimu
    total_found = online_found + total_ai
    print(f"\n  下载:{online_found}  LRCMaker:{ai_lrcmaker}  zimu:{ai_zimu}  已有:{skipped_had}  跳过:{skipped_user}")


# ═══════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════

def main():
    if '--gui' in sys.argv:
        run_gui()
        return

    if len(sys.argv) >= 3:
        cmd = sys.argv[1].lower()
        root = sys.argv[2]
        if not os.path.isdir(root):
            safe_print(f"目录不存在: {root}")
            safe_exit()
        handlers = {'tag': cmd_tag, 'fix': cmd_fix, 'check': cmd_check, 'lyrics': cmd_lyrics_interactive}
        if cmd in handlers:
            try:
                handlers[cmd](root)
            except Exception as e:
                safe_print(f"错误: {e}")
                import traceback; traceback.print_exc()
        else:
            safe_print(f"未知命令: {cmd}")
        safe_exit()

    # 无参数 / 双击运行 → 交互菜单
    run_menu()


if __name__ == '__main__':
    main()
