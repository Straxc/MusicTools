#!/usr/bin/env python3
"""
道理鱼音乐 - 歌手分隔符统一脚本 (Linux/Python版)
扫描音乐目录, 将 ARTIST 字段中的常见分隔符替换为 /
支持 FLAC / MP3

用法:
  python3 fix_artists.py /vol1/1000/music               # 交互模式
  python3 fix_artists.py /vol1/1000/music --yes         # 自动模式（Cron 用）
  python3 fix_artists.py /vol1/1000/music --backup      # 仅备份不修改
"""
import os, sys, re, shutil, json, time, struct

DEFAULT_SEP_RX = r',;&，；、|/\\\\'


def _build_sep_rx(extra=""):
    base = r'\s*(?:[' + DEFAULT_SEP_RX
    if extra:
        for c in extra: base += re.escape(c) + "|"
    base += r']|feat\.|ft\.|with)\s*'
    return re.compile(base)


SEP_RX = _build_sep_rx()


def fix_artist(artist, target="/", extra=""):
    if not artist: return artist
    rx = _build_sep_rx(extra)
    parts = [p.strip() for p in re.split(rx, artist) if p.strip()]
    return target.join(parts)


def read_tags_flac(fp):
    tags = {}
    with open(fp, 'rb') as f: data = f.read(4 * 1024 * 1024)
    if data[:4] != b'fLaC': return tags
    pos = 4
    while pos + 4 <= len(data):
        hdr = data[pos:pos + 4]
        bt, last = hdr[0] & 0x7F, hdr[0] & 0x80
        bl = struct.unpack('>I', b'\x00' + hdr[1:4])[0]
        if bt == 4 and pos + 4 + bl <= len(data):
            vc = data[pos + 4:pos + 4 + bl]
            vl = struct.unpack('<I', vc[:4])[0]
            if 8 + vl <= len(vc):
                nt = struct.unpack('<I', vc[4 + vl:8 + vl])[0]
                off = 8 + vl
                for _ in range(nt):
                    if off + 4 > len(vc): break
                    tl = struct.unpack('<I', vc[off:off + 4])[0]
                    if off + 4 + tl > len(vc): break
                    t = vc[off + 4:off + 4 + tl].decode('utf-8', 'replace')
                    if '=' in t: k, v = t.split('=', 1); tags[k.upper()] = v
                    off += 4 + tl
            break
        pos += 4 + bl
        if last: break
    return tags


def write_flac_tags(fp, updates):
    with open(fp, 'rb') as f: data = bytearray(f.read())
    if data[:4] != b'fLaC': return False
    pos = 4
    blocks = []
    while pos < len(data):
        hdr = data[pos:pos + 4]; bt = hdr[0] & 0x7F; last = hdr[0] & 0x80
        bl = struct.unpack('>I', b'\x00' + hdr[1:4])[0]
        blocks.append({'type': bt, 'offset': pos, 'length': bl, 'is_last': bool(last)})
        pos += 4 + bl
        if last: break
    audio_start = pos
    vc_idx = next((i for i, b in enumerate(blocks) if b['type'] == 4), None)
    if vc_idx is None: return False
    vc = data[blocks[vc_idx]['offset'] + 4:blocks[vc_idx]['offset'] + 4 + blocks[vc_idx]['length']]
    vl = struct.unpack('<I', vc[:4])[0]
    nt = struct.unpack('<I', vc[4 + vl:8 + vl])[0]
    off = 8 + vl
    existing = {}
    for _ in range(nt):
        tl = struct.unpack('<I', vc[off:off + 4])[0]
        t = vc[off + 4:off + 4 + tl].decode('utf-8', 'replace')
        if '=' in t: k, v = t.split('=', 1); existing[k.upper()] = v
        off += 4 + tl
    for k, v in updates.items(): existing[k] = v
    tags = [f"{k}={v}" for k, v in existing.items()]
    new_vc = bytearray()
    new_vc += struct.pack('<I', len(vc[4:4 + vl])); new_vc += vc[4:4 + vl]
    new_vc += struct.pack('<I', len(tags))
    for t in tags:
        tb = t.encode('utf-8'); new_vc += struct.pack('<I', len(tb)); new_vc += tb
    result = bytearray(b'fLaC')
    for i, b in enumerate(blocks):
        bt = b['type'] | (0x80 if i == len(blocks) - 1 else 0)
        if i == vc_idx:
            result.append(bt); result += struct.pack('>I', len(new_vc))[1:4]; result += new_vc
        else:
            d = data[b['offset'] + 4:b['offset'] + 4 + b['length']]
            result.append(bt); result += struct.pack('>I', b['length'])[1:4]; result += d
    result += data[audio_start:]
    with open(fp, 'wb') as f: f.write(result)
    return True


def read_tags_mp3(fp):
    tags = {}
    try:
        import mutagen.mp3
        mp3 = mutagen.mp3.MP3(fp)
        if mp3.tags:
            for fid, key in [('TPE1', 'ARTIST'), ('TIT2', 'TITLE'), ('TALB', 'ALBUM')]:
                f = mp3.tags.get(fid)
                if f: tags[key] = str(f)
    except: pass
    return tags


def write_tags_mp3(fp, updates):
    try:
        import mutagen.id3, mutagen.mp3
        mp3 = mutagen.mp3.MP3(fp)
        if mp3.tags is None: mp3.tags = mutagen.id3.ID3()
        if 'ARTIST' in updates:
            mp3.tags.delall('TPE1')
            mp3.tags.add(mutagen.id3.TPE1(encoding=3, text=updates['ARTIST']))
        if 'ALBUMARTIST' in updates:
            mp3.tags.delall('TPE2')
            mp3.tags.add(mutagen.id3.TPE2(encoding=3, text=updates['ALBUMARTIST']))
        mp3.save()
        return True
    except: return False


def main():
    if len(sys.argv) < 2:
        print("用法: python3 fix_artists.py <音乐目录> [--yes] [--backup]")
        sys.exit(1)

    root_dir = sys.argv[1]
    auto_yes = '--yes' in sys.argv
    backup_only = '--backup' in sys.argv

    if not os.path.isdir(root_dir):
        print(f"目录不存在: {root_dir}")
        sys.exit(1)

    # 交互: 选择要处理的字段
    target_sep = "/"
    extra_sep = ""
    fields_to_check = ('ARTIST', 'ALBUMARTIST')
    if not auto_yes and not backup_only:
        print("道理鱼音乐 - 音频多歌手分隔符统一工具")
        print("会递归处理指定目录及其所有子目录；先预览，确认后才写入。\n")
        r = input("要处理的标签字段 [artist/albumartist] (默认两者, a=artist, b=albumartist): ").strip().lower()
        if r == 'q': return
        if r == 'a': fields_to_check = ('ARTIST',)
        elif r == 'b': fields_to_check = ('ALBUMARTIST',)

        r = input(f"统一替换成的分隔符 [{target_sep}]: ").strip()
        if r and r != 'q': target_sep = r
        if r == 'q': return

        print("额外要识别的分隔符，可留空，多个请直接连续输入")
        print("注意: 输入 - 会破坏 Plum - Melodic Artist 这类艺人名")
        r = input(": ").strip()
        if r and r != 'q':
            if '-' in r:
                r = r.replace('-', '')
                print("  已自动移除 -（保护复合艺人名）")
            extra_sep = r
        if r == 'q': return

    # 扫描
    files_changes = []
    total_scanned = 0
    for dirpath, _, filenames in os.walk(root_dir):
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in ('.flac', '.mp3'): continue
            total_scanned += 1
            fp = os.path.join(dirpath, fn)
            tags = read_tags_flac(fp) if ext == '.flac' else read_tags_mp3(fp)
            changes = []
            for field in fields_to_check:
                val = tags.get(field, '')
                fixed = fix_artist(val, target_sep, extra_sep)
                if fixed != val:
                    changes.append((field, val, fixed))
            if changes:
                files_changes.append((fp, ext, changes))

    changed_count = len(files_changes)
    print(f"\n扫描音频: {total_scanned}")
    print(f"预览到: {changed_count}")
    print(f"无需修改: {total_scanned - changed_count}")

    if not changed_count:
        print("处理错误: 0")
        return

    # 预览
    for fp, ext, changes in files_changes:
        for field, orig, fixed in changes:
            print(f"  {field}: '{orig}' -> '{fixed}'")
            print(f"    {os.path.relpath(fp, root_dir)}")

    # 备份
    do_backup = False
    if not auto_yes:
        r = input("\n写入前是否为被修改的音频创建.bak备份? (y/N): ").strip().lower()
        do_backup = r == 'y'
    else:
        do_backup = False  # --yes 自动模式默认不备份

    if do_backup:
        bk = os.path.join(root_dir, f"备份_{time.strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(bk, exist_ok=True)
        for fp, ext, changes in files_changes:
            dst = os.path.join(bk, os.path.relpath(fp, root_dir))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(fp, dst)
        print(f"已备份到: {bk}")

    if backup_only: return

    if not auto_yes:
        r = input("\n确认把以上变化写入音频内嵌信息吗? (y/N): ").strip().lower()
        if r != 'y':
            print("已取消")
            return

    error_count = 0
    for fp, ext, changes in files_changes:
        updates = {field: fixed for field, orig, fixed in changes}
        ok = write_flac_tags(fp, updates) if ext == '.flac' else write_tags_mp3(fp, updates)
        if not ok: error_count += 1

    print(f"\n处理结果")
    print(f"扫描音频: {total_scanned}")
    print(f"已修改: {changed_count}")
    print(f"无需修改: {total_scanned - changed_count}")
    print(f"处理错误: {error_count}")


if __name__ == '__main__':
    main()
