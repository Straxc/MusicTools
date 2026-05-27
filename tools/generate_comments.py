#!/usr/bin/env python3
r"""
generate_comments.py — 扫描本地音乐库, 调用网易云增强API获取热门评论,
生成 music-comments-api 所需的 comments_data.json

用法:
  python generate_comments.py <音乐目录> [输出路径]
  python generate_comments.py G:\音乐\依睐\out  comments_data.json

API 文档: https://neteaseapi.gksm.store/docs/
评论格式: https://github.com/uparrows/music-comments-api
"""
import os, sys, json, time
from urllib.request import Request, urlopen
from urllib.parse import quote

# 你的增强 API 地址 — 配置在阿里云 VPS 上
API_BASE = "http://106.15.48.55:3000"

# 音乐文件扩展名
AUDIO_EXTS = {'.flac', '.mp3', '.wav', '.ogg', '.m4a', '.ape', '.wma'}

def api_get(path, params=None):
    """调用增强API, 返回 JSON"""
    url = f"{API_BASE}{path}"
    if params:
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        url += "?" + qs
    try:
        req = Request(url, headers={"User-Agent": "MusicTools/1.2"})
        resp = urlopen(req, timeout=30)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [API错误] {path}: {e}")
        return None

def search_song(title, artist):
    """搜索歌曲, 返回最佳匹配的 song id, 或 None"""
    # 用歌名+歌手搜索
    keywords = f"{title} {artist}".strip() if artist else title
    data = api_get("/search", {"keywords": keywords, "type": 1, "limit": 5})
    if not data or data.get("code") != 200:
        return None

    result = data.get("result", {})
    songs = result.get("songs", [])
    if not songs:
        return None

    best = songs[0]
    return {
        "id": best["id"],
        "name": best["name"],
        "artist": best.get("ar", [{}])[0].get("name", ""),
        "album": best.get("al", {}).get("name", "")
    }

def get_comments_mixed(song_id, hot_limit=10, reg_limit=10):
    """获取评论: hot_limit 条热门 + reg_limit 条普通, 去重"""
    total = max(hot_limit + reg_limit, 30)
    data = api_get("/comment/music", {"id": song_id, "limit": total})
    if not data or data.get("code") != 200:
        return []

    hot = data.get("hotComments", [])[:hot_limit]
    regular = data.get("comments", [])[:reg_limit + 5]  # 多取几个防去重裁剪

    seen = set()
    result = []
    for c in hot + regular:
        cid = c.get("commentId")
        if cid and cid not in seen:
            seen.add(cid)
            result.append(c)
    return result[:hot_limit + reg_limit]

def sanitize(text):
    """清除非法 Unicode 控制字符 (行分隔符 U+2028, 段分隔符 U+2029 等)"""
    if not isinstance(text, str):
        return text
    return text.translate(str.maketrans('', '', '\u2028\u2029\u0000\u0001\u0002\u0003\u0004\u0005\u0006\u0007\u0008\u000b\u000c\u000e\u000f\u0010\u0011\u0012\u0013\u0014\u0015\u0016\u0017\u0018\u0019\u001a\u001b\u001c\u001d\u001e\u001f'))


def format_comment(c, song_name, singer_name, album_name):
    """将增强API的评论对象转为 comments_data.json 格式"""
    user = c.get("user", {})
    return {
        "nick": sanitize(user.get("nickname", "匿名")),
        "avatarurl": sanitize(user.get("avatarUrl", "")),
        "content": sanitize(c.get("content", "")),
        "praiseNum": c.get("likedCount", 0),
        "createTime": int(c.get("time", 0) // 1000),  # ms → Unix秒
        "commentId": str(c.get("commentId", "")),
        "song_name": song_name,
        "singer_name": singer_name,
        "album_name": album_name
    }

def collect_audio_files(root_dir):
    """扫描目录收集音频文件"""
    files = []
    for dirpath, _, filenames in os.walk(root_dir):
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                files.append(os.path.join(dirpath, fn))
    return sorted(files)

def read_tags(fp):
    """简化版标签读取 — 仅 FLAC + MP3"""
    import struct
    ext = os.path.splitext(fp)[1].lower()
    tags = {}
    if ext == '.flac':
        with open(fp, 'rb') as f:
            data = f.read(4 * 1024 * 1024)
        if len(data) < 4 or data[:4] != b'fLaC':
            return tags
        pos = 4
        while pos + 4 <= len(data):
            hdr = data[pos:pos+4]
            bt, last = hdr[0] & 0x7F, hdr[0] & 0x80
            bl = struct.unpack('>I', b'\x00' + hdr[1:4])[0]
            if bt == 4 and pos + 4 + bl <= len(data):
                vc = data[pos+4:pos+4+bl]
                vl = struct.unpack('<I', vc[:4])[0]
                if 8 + vl <= len(vc):
                    nt = struct.unpack('<I', vc[4+vl:8+vl])[0]
                    off = 8 + vl
                    for _ in range(nt):
                        if off + 4 > len(vc): break
                        tl = struct.unpack('<I', vc[off:off+4])[0]
                        if off + 4 + tl > len(vc): break
                        t = vc[off+4:off+4+tl].decode('utf-8', errors='replace')
                        if '=' in t:
                            k, v = t.split('=', 1)
                            tags[k.upper()] = v
                        off += 4 + tl
                break
            pos += 4 + bl
            if last: break
    elif ext == '.mp3':
        try:
            import mutagen.mp3
            mp3 = mutagen.mp3.MP3(fp)
            if mp3.tags:
                for fid, key in [('TIT2', 'TITLE'), ('TPE1', 'ARTIST'), ('TALB', 'ALBUM')]:
                    f = mp3.tags.get(fid)
                    if f: tags[key] = str(f)
        except: pass
    return tags

def load_existing_comments(filepath):
    """加载已有评论数据, 返回 (评论列表, 已覆盖的歌曲key集合)"""
    if not os.path.isfile(filepath):
        return [], set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        return [], set()
    covered = set()
    for c in existing:
        covered.add((c.get('song_name', ''), c.get('singer_name', ''), c.get('album_name', '')))
    print(f"  已有 {len(existing)} 条评论, 覆盖 {len(covered)} 首歌曲")
    return existing, covered


def main():
    if len(sys.argv) < 2:
        print("用法: python generate_comments.py <音乐目录> [输出文件] [--full]")
        print("  --full  强制全量重新扫描 (默认: 增量, 跳过已有评论的歌曲)")
        sys.exit(1)

    root = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "comments_data.json"
    force_full = '--full' in sys.argv

    if not os.path.isdir(root):
        print(f"目录不存在: {root}")
        sys.exit(1)

    # 0. 加载已有评论
    existing_comments, covered = load_existing_comments(output_path) if not force_full else ([], set())

    # 1. 收集歌曲列表
    seen = set()
    song_list = []
    skipped = 0
    print(f"扫描: {root}")
    for fp in collect_audio_files(root):
        tags = read_tags(fp)
        title = (tags.get('TITLE') or os.path.splitext(os.path.basename(fp))[0]).strip()
        artist = tags.get('ARTIST', '').strip()
        album = tags.get('ALBUM', '').strip()
        key = (title, artist, album)
        if key in seen:
            continue
        seen.add(key)
        if key in covered:
            skipped += 1
            continue
        song_list.append(key)
    print(f"  {len(song_list)} 首新歌, 跳过 {skipped} 首已有评论")

    # 2. 搜索 + 获取评论
    new_comments = []
    for idx, (title, artist, album) in enumerate(song_list):
        print(f"[{idx+1}/{len(song_list)}] 搜索: {title[:30]} — {artist[:20]}")

        song = search_song(title, artist)
        if not song:
            print(f"  未匹配到歌曲, 跳过")
            continue

        print(f"  匹配: [{song['id']}] {song['name'][:30]} — {song['artist'][:20]}")
        comments = get_comments_mixed(song['id'], hot_limit=10, reg_limit=10)

        if not comments:
            print(f"  无评论")
            continue

        for c in comments:
            new_comments.append(format_comment(c, title, artist, album))
        print(f"  获取 {len(comments)} 条评论")

        time.sleep(0.3)

    # 3. 合并已有 + 新增
    all_comments = existing_comments + new_comments
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_comments, f, ensure_ascii=False, indent=2)

    print(f"\n完成: {len(new_comments)} 条新增 (+{len(existing_comments)} 已有) → {output_path}")

if __name__ == "__main__":
    main()
