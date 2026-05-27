#!/usr/bin/env python3
r"""
music-tag-web Subsonic 收藏自动同步脚本
将所有已入库歌曲添加到指定用户的 Subsonic 收藏，确保 Subsonic 客户端能看到全库

用法:
  python3 sync_subsonic_stars.py https://subsonic.str.ccwu.cc 用户名 密码
  python3 sync_subsonic_stars.py https://subsonic.str.ccwu.cc 用户名 密码 --dry-run  # 仅预览
"""

import sys, json, time, hashlib, random, string
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

def subsonic_call(server, user, password, endpoint, params=None):
    """调用 Subsonic REST API"""
    if params is None:
        params = {}
    params['u'] = user
    params['p'] = password
    params['c'] = 'sync_tool'
    params['f'] = 'json'
    params['v'] = '1.16.0'

    url = f"{server.rstrip('/')}/rest/{endpoint}?{urlencode(params)}"
    try:
        req = Request(url, headers={"User-Agent": "MusicSync/1.0"})
        resp = urlopen(req, timeout=30)
        data = json.loads(resp.read().decode('utf-8'))
        code = data.get('subsonic-response', {}).get('status')
        if code == 'failed':
            error = data.get('subsonic-response', {}).get('error', {})
            print(f"  API错误: {error.get('message', 'unknown')}")
            return None
        return data.get('subsonic-response', {})
    except HTTPError as e:
        print(f"  HTTP {e.code}: {e.reason}")
        return None
    except Exception as e:
        print(f"  错误: {e}")
        return None


def get_all_album_ids(server, user, password):
    """获取所有专辑 ID，每次取 500 个"""
    all_ids = []
    offset = 0
    while True:
        resp = subsonic_call(server, user, password, "getAlbumList2", {
            "type": "alphabeticalByName",
            "size": 500,
            "offset": offset,
        })
        if not resp: break
        albums = resp.get("albumList2", {}).get("album", [])
        if not albums: break
        for album in albums:
            all_ids.append(album["id"])
        if len(albums) < 500: break
        offset += 500
    return all_ids


def main():
    if len(sys.argv) < 4:
        print("用法: python3 sync_subsonic_stars.py <服务器地址> <用户名> <密码> [--dry-run]")
        print("示例: python3 sync_subsonic_stars.py https://subsonic.str.ccwu.cc admin password")
        sys.exit(1)

    server = sys.argv[1]
    user = sys.argv[2]
    password = sys.argv[3]
    dryrun = '--dry-run' in sys.argv

    print(f"服务器: {server}")
    print(f"用户:   {user}")
    if dryrun: print("模式:   DRY RUN (不实际star)")
    print()

    # 1. 获取所有专辑
    print("获取专辑列表...")
    album_ids = get_all_album_ids(server, user, password)
    print(f"  找到 {len(album_ids)} 个专辑")

    # 2. 获取每个专辑的歌曲
    all_track_ids = []
    for i, aid in enumerate(album_ids):
        resp = subsonic_call(server, user, password, "getAlbum", {"id": aid})
        if not resp: continue
        tracks = resp.get("album", {}).get("song", [])
        for track in tracks:
            all_track_ids.append(track["id"])
        if (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{len(album_ids)} 专辑, 已获取 {len(all_track_ids)} 首歌曲")

    print(f"  共获取 {len(all_track_ids)} 首歌曲")

    # 3. 获取已有收藏（不重复star）
    print("获取已有收藏...")
    starred_resp = subsonic_call(server, user, password, "getStarred2")
    starred_ids = set()
    if starred_resp:
        for song in starred_resp.get("starred2", {}).get("song", []):
            starred_ids.add(song["id"])
    print(f"  已有 {len(starred_ids)} 首收藏")

    # 4. 同步
    to_star = [tid for tid in all_track_ids if tid not in starred_ids]
    print(f"  需要收藏: {len(to_star)} 首")

    if dryrun:
        print("\n[Dry Run] 将收藏以下歌曲 (前10首):")
        for tid in to_star[:10]:
            print(f"  {tid}")
        if len(to_star) > 10:
            print(f"  ... 共 {len(to_star)} 首")
        return

    if not to_star:
        print("\n无需同步，所有歌曲已收藏")
        return

    print(f"\n开始收藏 {len(to_star)} 首歌曲...")
    count = 0
    for tid in to_star:
        resp = subsonic_call(server, user, password, "star", {"id": tid})
        if resp:
            count += 1
        if count % 100 == 0:
            print(f"  进度: {count}/{len(to_star)}")
        time.sleep(0.1)  # 避免请求过快

    print(f"\n完成! 已收藏 {count} 首歌曲")


if __name__ == "__main__":
    main()
