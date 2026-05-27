#!/usr/bin/env python3
"""
zimu_lrc.py — 调用 zimu-agent 语音识别并生成逐字 LRC
完全复制网页前端 (app.js) 的处理流程
用法: python zimu_lrc.py <音频文件> [输出.lrc]
"""

import sys, os, json, re, math, time, subprocess


# ═══════════════════════════════════════════
#  网页前端 js 函数的 Python 复刻
# ═══════════════════════════════════════════

PUNCT_SET = set("，。！？；：,.!?;:")
PUNCT_RE = re.compile(r'[，。！？；：,.!?;:]')


def parse_time_to_seconds(value):
    """复刻 parseTimeToSeconds"""
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        try:
            parts = [float(p) for p in text.split(":")]
        except ValueError:
            return None
        if any(math.isnan(n) for n in parts):
            return None
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return None
    try:
        n = float(text)
        return n if math.isfinite(n) else None
    except ValueError:
        return None


def format_lrc_time(seconds):
    """复刻 formatLrcTime — mm:ss.xx (百分秒 floor)"""
    if not isinstance(seconds, (int, float)) or math.isnan(seconds):
        return "00:00.00"
    total_cs = max(0, math.floor(seconds * 100))
    cs = total_cs % 100
    total_sec = total_cs // 100
    s = total_sec % 60
    m = total_sec // 60
    return f"{m:02d}:{s:02d}.{cs:02d}"


def to_comma_only(text):
    """复刻 toCommaOnlyForSrt"""
    t = text.replace("\r", "\n").replace("\n", "")
    t = PUNCT_RE.sub('，', t)
    t = re.sub(r'，{2,}', '，', t)
    t = re.sub(r'^，|，$', '', t)
    return t.strip()


def normalize_subtitle_text(text, preserve_punctuation=False):
    """复刻 normalizeSubtitleText"""
    raw = text.replace("\r", "\n").replace("\n", "").strip()
    if not preserve_punctuation:
        return to_comma_only(raw)
    return re.sub(r'\s+', ' ', raw).strip()


def plain_len(text):
    """复刻 plainLen"""
    return len(re.sub(r'[\s，。！？；：,.!?;:]+', '', str(text or '')))


def strip_speech_repair_text(text):
    """复刻 stripSpeechRepairText"""
    s = str(text or '').replace("\r", "\n").replace("\n", "")
    return list(re.sub(r'[\s，。！？；：,.!?;:]+', '', s))


def split_cue_tail_punctuation(text):
    """复刻 splitCueTailPunctuation"""
    cleaned = str(text or '').replace("\r", "\n").replace("\n", "").strip()
    m = re.match(r'^(.*?)([，。！？；：,.!?;:]+)?$', cleaned)
    if m:
        return {'body': m.group(1) or '', 'tail': m.group(2) or ''}
    return {'body': cleaned, 'tail': ''}


# ── 原子段合并 ──

def merge_atomic_segments(seg_list):
    """复刻 mergeAtomicSegments"""
    if not seg_list:
        return []
    avg_len = sum(len(str(s.get('text', '')).strip()) for s in seg_list) / len(seg_list)
    tiny_ratio = sum(1 for s in seg_list if len(str(s.get('text', '')).strip()) <= 2) / max(len(seg_list), 1)
    if avg_len > 2.2 and tiny_ratio < 0.65:
        return seg_list

    merged = []
    cur = None

    def flush():
        nonlocal cur
        if cur is None:
            return
        txt = str(cur['text']).strip()
        if txt and cur['end_time'] > cur['start_time']:
            cur['text'] = txt
            merged.append(dict(cur))
        cur = None

    for seg in seg_list:
        text = str(seg.get('text', '')).strip()
        if not text:
            continue
        start = float(seg.get('start_time', 0))
        end = float(seg.get('end_time', 0))
        if not (math.isfinite(start) and math.isfinite(end) and end > start):
            continue
        if cur is None:
            cur = {'start_time': start, 'end_time': end, 'text': text}
            continue
        gap = start - cur['end_time']
        cur_text = str(cur.get('text', ''))
        cur_plen = plain_len(cur_text)
        cur_dur = cur['end_time'] - cur['start_time']
        should_break = (
            gap > 0.45
            or (cur_text and cur_text[-1] in PUNCT_SET and cur_plen >= 6)
            or cur_plen >= 26
            or cur_dur >= 3.8
        )
        if should_break:
            flush()
            cur = {'start_time': start, 'end_time': end, 'text': text}
        else:
            cur['end_time'] = max(cur['end_time'], end)
            cur['text'] = cur_text + text

    flush()
    return merged if merged else seg_list


# ── 根据停顿注入标点 ──

def inject_pause_punctuation(seg_list):
    """复刻 injectPausePunctuation"""
    if not seg_list:
        return []
    comma_pause = 0.28
    period_pause = 0.58
    min_chars = 6
    break_chars = 18

    def has_tail_punct(t):
        return bool(re.search(r'[，。！？；：,.!?;:]$', str(t or '').strip()))

    def looks_like_question(t):
        t = re.sub(r'[，。！？；：,.!?;:]+$', '', str(t or '').strip())
        if not t:
            return False
        if re.search(r'[吗呢么]$', t):
            return True
        for p in ["是不是", "是否", "可不可以", "能不能", "会不会",
                  "有没有", "要不要", "怎么", "怎样", "如何",
                  "为什么", "为何", "多少", "哪里", "哪儿", "哪个", "哪位", "谁"]:
            if p in t:
                return True
        return False

    def ensure_tail(t, punct_ch):
        t = str(t or '').strip()
        if not t:
            return t
        if has_tail_punct(t):
            return t
        chosen = "？" if looks_like_question(t) else punct_ch
        return t + chosen if chosen else t

    out = []
    for idx, seg in enumerate(seg_list):
        start = float(seg.get('start_time', 0))
        end = float(seg.get('end_time', 0))
        text = str(seg.get('text', '')).strip()
        if not (math.isfinite(start) and math.isfinite(end) and end > start and text):
            continue

        is_last = idx == len(seg_list) - 1
        next_start = float(seg_list[idx + 1].get('start_time', 0)) if not is_last else None
        gap = max(0, next_start - end) if next_start is not None else 0
        chars = plain_len(text)

        punct = ""
        if not has_tail_punct(text):
            if is_last or gap >= period_pause:
                punct = "。"
            elif (gap >= comma_pause and chars >= min_chars) or chars >= break_chars:
                punct = "，"

        out.append({
            'start_time': start,
            'end_time': end,
            'text': ensure_tail(text, punct),
        })
    return out if out else seg_list


# ── 段标准化 ──

def normalize_segments_for_srt(segments):
    """复刻 normalizeSegmentsForSrt"""
    if not isinstance(segments, list):
        return []

    seg_list = []
    for seg in segments:
        start = parse_time_to_seconds(seg.get('start_time'))
        end = parse_time_to_seconds(seg.get('end_time'))
        text = str(seg.get('text', '')).replace("\r", "\n").replace("\n", "").strip()
        if start is None or end is None or end <= start or not text:
            continue
        seg_list.append({'start_time': start, 'end_time': end, 'text': text})

    seg_list.sort(key=lambda s: (s['start_time'], s['end_time']))
    merged = merge_atomic_segments(seg_list)
    puncted = inject_pause_punctuation(merged)

    normalized = []
    for seg in puncted:
        start = seg['start_time']
        end = seg['end_time']
        if normalized:
            prev = normalized[-1]
            if start < prev['end_time']:
                start = prev['end_time']
        if end - start < 0.05:
            continue
        normalized.append({
            'start_time': start,
            'end_time': end,
            'text': str(seg.get('text', '')).replace("\r", "\n").replace("\n", "").strip(),
        })
    return normalized


# ── 短句分割 ──

def split_by_max_len(text, max_len=14):
    """复刻 splitByMaxLen"""
    limit = max(6, int(max_len) if max_len else 14)
    rest = str(text or '').strip()
    out = []
    while len(rest) > limit:
        probe = rest[:limit + 1]
        cut = -1
        for m in PUNCT_RE.finditer(probe):
            cut = m.start() + 1
        if cut < math.floor(limit * 0.55):
            space = probe.rfind(" ")
            if space >= math.floor(limit * 0.55):
                cut = space + 1
        if cut <= 0 or cut > len(probe):
            cut = limit
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return [x for x in out if x]


def compact_tiny_chunks(chunks):
    """复刻 compactTinyChunks"""
    if len(chunks) <= 1:
        return chunks
    out = []
    for chunk in chunks:
        cur = str(chunk or '').strip()
        if not cur:
            continue
        if not out:
            out.append(cur)
            continue
        if plain_len(cur) <= 2:
            out[-1] = out[-1] + cur
        else:
            out.append(cur)
    return out


def split_short_lines(text, max_len=14):
    """复刻 splitShortLines"""
    cleaned = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not cleaned:
        return []
    parts = re.findall(r'[^，。！？；：,.!?;:]+[，。！？；：,.!?;:]*', cleaned)
    if not parts:
        return split_by_max_len(cleaned, max(max_len, 22))
    out = []
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if len(piece) > max(max_len * 1.8, 28):
            for x in split_by_max_len(piece, max(max_len, 22)):
                out.append(x)
        else:
            out.append(piece)
    return compact_tiny_chunks([x for x in out if x])


# ── 构建短句列表 ──

def build_short_cue_list(segments):
    """复刻 buildShortCueList"""
    normalized = normalize_segments_for_srt(segments)
    cues = []
    for seg in normalized:
        start = parse_time_to_seconds(seg.get('start_time'))
        end = parse_time_to_seconds(seg.get('end_time'))
        if start is None or end is None or end <= start:
            continue
        text = to_comma_only(seg.get('text', ''))
        if not text:
            continue

        duration = end - start
        if duration <= 2.6 and len(text) <= 26:
            cues.append({'start': start, 'end': end, 'text': text})
            continue

        max_len = 20 if duration > 5.0 else 24
        chunks = split_short_lines(text, max_len)
        chunks = [to_comma_only(c) for c in chunks]
        chunks = [c for c in chunks if c]

        if len(chunks) <= 1:
            cues.append({'start': start, 'end': end, 'text': text})
            continue

        weights = []
        for chunk in chunks:
            w = len(re.sub(r'[，。！？；：,.!?;:]', '', chunk))
            weights.append(w if w > 0 else 1)
        total_w = sum(weights) or 1
        durations = [duration * (w / total_w) for w in weights]

        if duration > 0.4 * len(chunks):
            durations = [max(0.18, d) for d in durations]
            sum_dur = sum(durations) or duration
            scale = duration / sum_dur
            durations = [d * scale for d in durations]

        cursor = start
        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            dur_slice = durations[idx] or 0
            next_cursor = end if is_last else min(end, cursor + dur_slice)
            if next_cursor > cursor:
                cues.append({'start': cursor, 'end': next_cursor, 'text': chunk})
            cursor = next_cursor

    return cues


# ── 文本修复 ──

def build_speech_cue_tokens(cues):
    """复刻 buildSpeechCueTokens"""
    if not cues:
        return []
    tokens = []
    for cue in cues:
        start = float(cue.get('start', 0))
        end = float(cue.get('end', 0))
        if not (math.isfinite(start) and math.isfinite(end) and end > start):
            continue
        body = strip_speech_repair_text(cue.get('text', ''))
        if not body:
            continue
        duration = max(0.001, end - start)
        for idx, ch in enumerate(body):
            char_start = start + duration * (idx / len(body))
            char_end = end if idx == len(body) - 1 else start + duration * ((idx + 1) / len(body))
            tokens.append({'text': ch, 'start': char_start, 'end': char_end})
    return tokens


def repair_speech_cue_texts(cues, full_text):
    """复刻 repairSpeechCueTexts"""
    if not cues:
        return {'cues': [], 'changed': False}

    truth_chars = strip_speech_repair_text(full_text)
    if not truth_chars:
        return {'cues': cues, 'changed': False}

    cue_meta = []
    for cue in cues:
        parts = split_cue_tail_punctuation(cue.get('text', ''))
        body = strip_speech_repair_text(parts['body'])
        cue_meta.append({'cue': cue, 'body': body, 'tail': parts['tail']})

    flattened = []
    for cue_idx, item in enumerate(cue_meta):
        for ch in item['body']:
            flattened.append({'ch': ch, 'cue_idx': cue_idx})

    if not flattened:
        return {'cues': cues, 'changed': False}

    repaired_bodies = [[] for _ in cue_meta]
    original_combined = ''.join(f['ch'] for f in flattened)
    lookahead = 16
    truth_idx = 0
    cue_idx = 0
    last_cue_idx = flattened[0]['cue_idx']

    def find_next_flat_match(target, start_i):
        limit = min(len(flattened), start_i + lookahead + 1)
        for i in range(start_i, limit):
            if flattened[i]['ch'] == target:
                return i
        return -1

    def find_next_truth_match(target, start_i):
        limit = min(len(truth_chars), start_i + lookahead + 1)
        for i in range(start_i, limit):
            if truth_chars[i] == target:
                return i
        return -1

    while truth_idx < len(truth_chars) and cue_idx < len(flattened):
        truth_char = truth_chars[truth_idx]
        cue_char = flattened[cue_idx]['ch']
        current_cue_idx = flattened[cue_idx]['cue_idx']

        if truth_char == cue_char:
            repaired_bodies[current_cue_idx].append(truth_char)
            last_cue_idx = current_cue_idx
            truth_idx += 1
            cue_idx += 1
            continue

        next_cue_match = find_next_flat_match(truth_char, cue_idx + 1)
        next_truth_match = find_next_truth_match(cue_char, truth_idx + 1)

        if next_truth_match != -1 and (next_cue_match == -1 or (next_truth_match - truth_idx) <= (next_cue_match - cue_idx)):
            target_cue_idx = last_cue_idx if isinstance(last_cue_idx, int) else current_cue_idx
            while truth_idx < next_truth_match:
                repaired_bodies[target_cue_idx].append(truth_chars[truth_idx])
                truth_idx += 1
            continue

        if next_cue_match != -1:
            cue_idx += 1
            continue

        repaired_bodies[current_cue_idx].append(truth_char)
        last_cue_idx = current_cue_idx
        truth_idx += 1
        cue_idx += 1

    append_cue_idx = last_cue_idx if isinstance(last_cue_idx, int) else (len(cue_meta) - 1 if cue_meta else 0)
    while truth_idx < len(truth_chars):
        repaired_bodies[append_cue_idx].append(truth_chars[truth_idx])
        truth_idx += 1

    repaired = []
    for idx, item in enumerate(cue_meta):
        new_cue = dict(item['cue'])
        new_cue['text'] = ''.join(repaired_bodies[idx]) + item['tail']
        repaired.append(new_cue)

    repaired_combined = ''.join(''.join(b) for b in repaired_bodies)
    changed = repaired_combined != original_combined
    return {'cues': repaired, 'changed': changed}


def regroup_speech_cues_by_truth_punctuation(cues, full_text):
    """复刻 regroupSpeechCuesByTruthPunctuation"""
    if not cues:
        return {'cues': [], 'changed': False}

    raw_text = str(full_text or '').replace("\r", "\n").replace("\n", "").strip()
    if not raw_text:
        return {'cues': cues, 'changed': False}

    tokens = build_speech_cue_tokens(cues)
    if not tokens:
        return {'cues': cues, 'changed': False}

    chars = list(raw_text)
    rebuilt = []
    token_idx = 0
    current_text = ""
    current_start = None
    current_end = None

    def flush():
        nonlocal current_text, current_start, current_end
        t = normalize_subtitle_text(current_text, True)
        if not t or current_start is None or current_end is None or current_end <= current_start:
            current_text = ""
            current_start = None
            current_end = None
            return
        rebuilt.append({'start': current_start, 'end': current_end, 'text': t})
        current_text = ""
        current_start = None
        current_end = None

    for ch in chars:
        if not ch:
            continue
        if ch.isspace():
            if current_text:
                current_text += ch
            continue
        if ch in PUNCT_SET:
            if current_text:
                current_text += ch
                flush()
            continue

        if token_idx >= len(tokens):
            continue
        token = tokens[token_idx]
        if current_start is None:
            current_start = token['start']
        current_end = token['end']
        current_text += ch
        token_idx += 1

    flush()

    if not rebuilt:
        return {'cues': cues, 'changed': False}

    original_texts = '|'.join(normalize_subtitle_text(c.get('text', ''), True) for c in cues)
    rebuilt_texts = '|'.join(c['text'] for c in rebuilt)
    changed = original_texts != rebuilt_texts or len(rebuilt) != len(cues)
    return {'cues': rebuilt, 'changed': changed}


# ── 构建 LRC ──

def build_lrc_from_cues(cues, preserve_punctuation=True):
    """复刻 buildLrcFromCues (preservePunctuation=True)"""
    if not cues:
        return ""
    lines = []
    for cue in cues:
        if not cue:
            continue
        start = cue.get('start', 0)
        end = cue.get('end', 0)
        if not (math.isfinite(start) and math.isfinite(end) and end > start):
            continue
        text = normalize_subtitle_text(cue.get('text', ''), preserve_punctuation)
        if not text:
            continue

        chars = list(text)
        if len(chars) <= 1:
            lines.append(f"[{format_lrc_time(start)}]{text}")
            continue

        duration = max(0.02, end - start)
        step = duration / len(chars)
        line = f"[{format_lrc_time(start)}]"
        for idx, ch in enumerate(chars):
            ts = start + step * idx
            line += f"<{format_lrc_time(ts)}>{ch}"
        lines.append(line)
    return "\n".join(lines)


# ── 提取纯文本 ──

def extract_plain_text(data):
    """复刻 extractPlainText"""
    if isinstance(data.get('text'), str) and data['text'].strip():
        return data['text'].replace("\r", "\n").replace("\n", "").strip()
    if isinstance(data.get('segments'), list):
        parts = [str(s.get('text', '')).replace("\r", "\n").replace("\n", "").strip()
                 for s in data['segments']]
        text = ''.join(p for p in parts)
        if text:
            return text
    raw = str(data.get('raw_text', '')).strip()
    if raw and (raw.startswith('[') or raw.startswith('{')):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get('text'):
                return str(parsed['text']).strip()
        except (json.JSONDecodeError, TypeError):
            pass
    return raw


# ═══════════════════════════════════════════
#  HTTP 请求 — 使用 curl (与服务器端开发者测试方式一致)
# ═══════════════════════════════════════════

def call_zimu_transcribe(audio_path):
    """使用 curl 发送 multipart 请求 — 浏览器级别可靠性"""
    audio_abs = os.path.abspath(audio_path)

    cmd = [
        'curl', '-s', '--max-time', '600',
        '-X', 'POST', 'http://127.0.0.1:5003/api/transcribe',
        '-F', f'file=@{audio_abs};type=audio/x-flac',
        '-F', 'model=qwen3',
        '-F', 'recognize_mode=speech',
        '-F', 'language=Chinese',
        '-F', 'max_new_tokens=256',
        '-F', 'align_srt=1',
        '-F', 'num_beams=1',
        '-F', 'temperature=0',
        '-F', 'top_p=1.0',
        '-F', 'repetition_penalty=1.0',
    ]

    print(f"  [curl] {' '.join(cmd[:3])} ... (音频: {os.path.basename(audio_path)})")
    t0 = time.time()

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=610,
                            encoding='utf-8', errors='replace')

    elapsed = time.time() - t0
    print(f"  [curl] 耗时 {elapsed:.0f}s (HTTP {len(result.stdout)} bytes)")

    if result.returncode != 0:
        raise RuntimeError(f"curl 失败 (code={result.returncode}): {result.stderr[:500]}")

    if not result.stdout.strip():
        raise RuntimeError(f"curl 返回空响应 (stderr: {result.stderr[:300]})")

    data = json.loads(result.stdout)

    if data.get('error'):
        raise RuntimeError(f"zimu 错误: {data['error']}")
    if not data.get('ok'):
        raise RuntimeError(f"zimu API 返回失败: {str(data)[:300]}")

    return data


# ═══════════════════════════════════════════
#  主流程 (复刻 computeTaskSrt)
# ═══════════════════════════════════════════

def process_transcribe_result(data):
    """复刻 computeTaskSrt 中 speech 模式的完整处理链路"""
    segments = data.get('segments', [])
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("API 响应中没有 segments")

    full_text = extract_plain_text(data)
    print(f"  [识别] {len(segments)} 原始段, 纯文本 {len(full_text)} 字符")

    # Step 1: buildShortCueList
    short_cues = build_short_cue_list(segments)
    print(f"  [短句] {len(short_cues)} 句")

    # Step 2: repairSpeechCueTexts
    repair_result = repair_speech_cue_texts(short_cues, full_text)
    short_cues = repair_result['cues']
    if repair_result['changed']:
        print(f"  [修复] ASR 文本已修复")

    # Step 3: regroupSpeechCuesByTruthPunctuation
    regroup_result = regroup_speech_cues_by_truth_punctuation(short_cues, full_text)
    short_cues = regroup_result['cues']
    if regroup_result['changed']:
        print(f"  [重组] 按标点重组为 {len(short_cues)} 句")

    # Step 4: buildLrcFromCues (preservePunctuation=True)
    lrc = build_lrc_from_cues(short_cues, preserve_punctuation=True)
    print(f"  [LRC] {lrc.count(chr(10)) + 1} 行, {len(lrc)} 字符")

    return lrc


# ═══════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("zimu_lrc.py — zimu-agent 语音识别 → 逐字 LRC")
        print("用法: python zimu_lrc.py <音频文件> [输出.lrc]")
        sys.exit(1)

    audio_path = sys.argv[1]
    if not os.path.isfile(audio_path):
        print(f"文件不存在: {audio_path}")
        sys.exit(1)

    output_path = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(audio_path)[0] + '.lrc'

    print(f"{'='*55}")
    print(f"  zimu_lrc — 语音识别 → 逐字LRC")
    print(f"  输入: {os.path.basename(audio_path)}")
    print(f"  输出: {os.path.basename(output_path)}")
    print(f"{'='*55}")

    try:
        # Step A: 调用 zimu-agent
        data = call_zimu_transcribe(audio_path)

        # Step B: 纯客户端 LRC 构建 (完全复刻网页 app.js)
        print(f"  [处理] 开始构建 LRC...")
        t0 = time.time()
        lrc = process_transcribe_result(data)
        print(f"  [处理] 耗时 {time.time() - t0:.1f}s")

        # Step C: 保存
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(lrc)

        print(f"\n  已保存: {output_path}")
        print(f"{'='*55}")
    except subprocess.TimeoutExpired:
        print(f"\n  [错误] curl 请求超时 (10分钟)")
        print(f"  请检查 zimu-agent 是否正常运行")
        sys.exit(1)
    except Exception as e:
        print(f"\n  [错误] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
