#!/usr/bin/env python3
"""Compile an explicit edit plan into separate CapCut tracks; publish a new draft.

Media must already be uploaded with the official capcut-upload workflow. No paid
generation, arbitrary URLs, flattened masters, or overwrite of an existing draft.
"""
import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from urllib.parse import urlsplit

CAPTION_STYLE = {'font': 'vimo://7317175475195875841', 'font_size': 6,
                 'alignment': 1, 'text_color': '#FFFFFFFF',
                 'border_width': 0, 'border_alpha': 0}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def builder(path):
    """Explicit installed official skill directory, not a downloaded module."""
    path = Path(path).expanduser().resolve()
    if not (path / 'SKILL.md').is_file() or not (path / 'scripts/_draft.py').is_file():
        raise ValueError('请提供已安装的官方 capcut-stdraft-builder Skill 目录')
    sys.path.insert(0, str(path / 'scripts'))
    import apply_materials, apply_captions, manage_materials, _draft
    return apply_materials, apply_captions, manage_materials, _draft


def ms(value, name, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(name + ' 必须是整数毫秒')
    return value


def compile_plan(plan, media, builder_root):
    """plan schema 1: picture/audio/captions/effects, all project times in ms.

    picture/audio rows: id,candidate,start_ms,duration_ms,source_start_ms.
    captions: text,start_ms,end_ms,audio_id (explicit source binding).
    effects: transition(from_id,to_id,duration_ms,source) or
             video_effect/filter(start_ms,duration_ms,source).
    Each media[candidate]: source=vid://..., duration_ms,width,height,sha256.
    """
    if plan.get('schema') != 1 or not plan.get('selection_digest'):
        raise ValueError('剪辑单缺少版本锁定指纹或 schema')
    a, captions, updates, validation = builder(builder_root)
    total = ms(plan['duration_ms'], '总时长', True)
    picture = plan['picture']
    if not picture or not plan['audio']:
        raise ValueError('需要独立画面片段和声音片段')
    draft = {'version': '0.1.0', 'segments': [{'id': 'draft', 'type': 'DRAFT',
        'attributes': {'width': ms(plan['width'], '宽', True),
                       'height': ms(plan['height'], '高', True), 'ratio_type': 'custom'}}]}
    ids, mapping, warnings, occupied = {}, [], [], []

    def take(result):
        nonlocal draft
        if not result['check_result']['valid']:
            raise ValueError('CapCut 工程校验未通过')
        draft = result['draft']
        warnings.extend(result.get('warnings', []))
        return next(iter(result.get('added_segment_ids', [])), None)

    def timing(row):
        start = ms(row['start_ms'], '成片入点')
        length = ms(row['duration_ms'], '片段时长', True)
        offset = ms(row.get('source_start_ms', 0), '源入点')
        if start + length > total:
            raise ValueError('片段超过成片时长')
        m = media[row['candidate']]
        if not m.get('source', '').startswith('vid://') or not m.get('sha256'):
            raise ValueError('媒体未完成上传或内容指纹核对')
        if offset + length > ms(m['duration_ms'], '媒体时长', True):
            raise ValueError('源出点超过实际媒体可用长度')
        if row['id'] in ids:
            raise ValueError('重复的剪辑片段 ID')
        return start, length, offset, m

    cursor = 0
    for row in picture:
        start, length, offset, m = timing(row)
        if start != cursor:
            raise ValueError('主画面必须按顺序连续排列；空隙或叠化先在剪辑单明确处理')
        sid = take(a.apply_video(draft, source=m['source'], start_ms=start,
            duration_ms=length, source_start_ms=offset, volume=0, layer=0))
        attrs = {k: m[k] for k in ('width', 'height') if k in m}
        if 'crop' in row:
            attrs['crop'] = row['crop']
        take(updates.update_material(draft, sid, **attrs))
        ids[row['id']] = sid
        mapping.append({'id': row['id'], 'segment_id': sid, 'kind': 'picture'})
        cursor += length
    if cursor != total:
        raise ValueError('画面总长与剪辑单不一致')

    roles = {}
    for row in plan['audio']:
        start, length, offset, m = timing(row)
        role = row.get('role', 'mixed')
        lane = 0
        # Reuse the lowest available lane of the same role; retain J/L overlaps.
        while (lane in roles and roles[lane] != role) or any(
                lane == l and start < right and left < start + length for l, left, right in occupied):
            lane += 1
        roles[lane] = role
        occupied.append((lane, start, start + length))
        sid = take(a.apply_audio(draft, source=m['source'], meta_type='MUSIC' if role == 'music' else 'SOUND',
            start_ms=start, duration_ms=length, source_start_ms=offset,
            volume=row.get('volume', 1), layer=lane))
        take(updates.update_material(draft, sid, **{'fade_in.ms': row.get('fade_in_ms', 0),
                                                 'fade_out.ms': row.get('fade_out_ms', 0)}))
        ids[row['id']] = sid
        mapping.append({'id': row['id'], 'segment_id': sid, 'kind': 'audio', 'role': role})

    for cue in plan.get('captions', []):
        row = next((r for r in plan['audio'] if r['id'] == cue['audio_id']), None)
        if row is None:
            raise ValueError('字幕必须绑定本轮的明确声音片段')
        start, end = ms(cue['start_ms'], '字幕入点'), ms(cue['end_ms'], '字幕出点')
        if not row['start_ms'] <= start < end <= row['start_ms'] + row['duration_ms']:
            raise ValueError('字幕区间超出绑定的声音片段')
        vid = media[row['candidate']]['source'].removeprefix('vid://')
        offset = row.get('source_start_ms', 0) - row['start_ms']
        take(captions.apply_captions(draft, [{'vid': vid, 'utterances': [
            {'text': cue['text'], 'start_time': start + offset, 'end_time': end + offset}]}],
            bindings={vid: [ids[row['id']]]},
            layout={'position_x': 0, 'position_y': -0.80}, text_style=CAPTION_STYLE))

    for effect in plan.get('effects', []):
        # stdraft_uri must be copied from a real material search response.
        if not effect.get('source', '').startswith('vimo://'):
            raise ValueError('请使用 CapCut 素材工具返回的完整 stdraft_uri')
        kind = effect['type']
        if kind == 'transition':
            take(a.apply_transition(draft, source=effect['source'],
                from_segment_id=ids[effect['from_id']], to_segment_id=ids[effect['to_id']],
                duration_ms=effect['duration_ms']))
        elif kind in ('video_effect', 'filter'):
            start, length = ms(effect['start_ms'], '特效入点'), ms(effect['duration_ms'], '特效时长', True)
            if start + length > total:
                raise ValueError('特效超出成片')
            fn = a.apply_video_effect if kind == 'video_effect' else a.apply_filter
            take(fn(draft, source=effect['source'], start_ms=start, duration_ms=length))
        else:
            raise ValueError('不支持的效果类型')

    # Cloud normalization sorts IDs before main-track insertion. Rename only
    # this newly authored document; never rename a user's existing cloud IDs.
    renamed = {}
    for kind in ('VIDEO', 'AUDIO', 'TEXT', 'VIDEO_EFFECT', 'COLOR_FILTER'):
        rows = sorted((s for s in draft['segments'] if s['type'] == kind),
                      key=lambda s: (s.get('timeline', {}).get('start.ms', 0), s['id']))
        for i, s in enumerate(rows, 1):
            renamed[s['id']] = f'{kind.lower()}:{i:06d}'
    for s in draft['segments']:
        s['id'] = renamed.get(s['id'], s['id'])
        if 'parent_id' in s:
            s['parent_id'] = renamed.get(s['parent_id'], s['parent_id'])
    for item in mapping:
        item['segment_id'] = renamed[item['segment_id']]
    model, _, _ = validation.load(draft)
    result = validation.finish(model, draft, [], [])
    if not result['check_result']['valid']:
        raise ValueError('最终工程校验失败')
    return {'draft': result['draft'], 'mapping': mapping,
            'selection_digest': plan['selection_digest'], 'warnings': sorted(set(warnings))}


def validate_inputs(plan, media, inputs):
    """The agent's editorial plan cannot silently substitute adopted footage."""
    selected = {r['candidate']: r for r in inputs['inputs']}
    if plan['selection_digest'] != inputs['selection_digest']:
        raise ValueError('剪辑单不属于本轮采用快照')
    used = {r['candidate'] for r in plan['picture']}
    audio_used = {r['candidate'] for r in plan['audio']}
    picture_selected = {key for key, row in selected.items() if row.get('media_role', 'video') == 'video'}
    if any(row.get('media_role', 'video') not in ('video', 'audio') for row in selected.values()):
        raise ValueError('锁定素材须明确为 video 或 audio')
    if used != picture_selected or used | audio_used != set(selected):
        raise ValueError('剪辑单遗漏或替换了本轮采用素材')
    for candidate, row in selected.items():
        if media[candidate]['sha256'] != row['sha256']:
            raise ValueError('上传映射与锁定媒体指纹不一致')
        from companion_store import sha
        if sha(row['path']) != row['sha256']:
            raise ValueError('本轮锁定媒体文件内容已改变')


def tracks(doc):
    return sorted((s for s in doc['segments'] if s['type'] in ('VIDEO', 'AUDIO', 'TEXT')
                   and s.get('attributes', {}).get('meta_type') != 'photo'
                   and not s['id'].endswith('_patch_0')), key=lambda s: s['id'])


def verify_roundtrip(expected, actual, tolerance_ms=2):
    """Check structure, source, project/source trims, muting and subtitle text.

    Cloud may reindex lanes and add PATCH styles; it must not reorder footage.
    This is structural verification, not a visual/audio quality approval.
    """
    original, received = tracks(expected), tracks(actual)
    problems = []
    if {s['id'] for s in original} != {s['id'] for s in received}:
        problems.append('分轨片段 ID 或数量改变')
    index = {s['id']: s for s in received}
    for left in original:
        right = index.get(left['id'])
        if not right:
            continue
        for key in ('start.ms', 'duration.ms'):
            if abs(left['timeline'][key] - right.get('timeline', {}).get(key, -999)) > tolerance_ms:
                problems.append(left['id'] + ' 成片时间改变')
        x, y = left['attributes'], right.get('attributes', {})
        if left['type'] in ('VIDEO', 'AUDIO'):
            for key, default in [('source', ''), ('start.ms', 0), ('volume', 1)]:
                u, v = x.get(key, default), y.get(key, default)
                same = math.isclose(u, v, abs_tol=0.000001) if isinstance(u, (int, float)) and isinstance(v, (int, float)) else u == v
                if not same:
                    problems.append(left['id'] + ' 来源、源入点或音量改变')
        elif x.get('content') != y.get('content'):
            problems.append(left['id'] + ' 字幕文本改变')
        if left['type'] == 'TEXT' and x.get('border_width', 0) == 0 and y.get('border_width', 0) != 0:
            problems.append(left['id'] + ' 字幕描边改变')
        for key in ('transition', 'transition.duration.ms'):
            if x.get(key) != y.get(key):
                problems.append(left['id'] + ' 转场设置改变')
        if left['type'] == 'VIDEO' and 'crop' in x and any(
                abs(u - v) > 0.00001 for u, v in zip(x['crop'], y.get('crop', [0, 0, 1, 1]))):
            problems.append(left['id'] + ' 画面裁切改变')
    for kind in ('VIDEO_EFFECT', 'COLOR_FILTER'):
        x = sorted((s['attributes'].get('source'), s.get('timeline', {}).get('start.ms'), s.get('timeline', {}).get('duration.ms')) for s in expected['segments'] if s['type'] == kind)
        y = sorted((s['attributes'].get('source'), s.get('timeline', {}).get('start.ms'), s.get('timeline', {}).get('duration.ms')) for s in actual['segments'] if s['type'] == kind)
        if x != y:
            problems.append(kind + ' 效果或时间改变')
    return sorted(set(problems))


def editor_url(value):
    p = urlsplit(value or '')
    if p.scheme != 'https' or p.hostname != 'www.capcut.com' or not p.path.startswith('/editor/') or p.username or p.password:
        raise ValueError('CapCut 未返回安全的网页编辑地址')
    return value


def publish_new(draft, receipt_path, selection_digest, bridge):
    """Persist session intent BEFORE push. Uncertain saves are never retried here."""
    path = Path(receipt_path)
    if path.exists():
        raise ValueError('此交付已有回执或待核对记录，请恢复该会话，不能盲目新建或重推')
    session = bridge.call('start_stdraft_session', {})
    sid = session['session_id']
    receipt = {'status': 'created', 'session_id': sid, 'selection_digest': selection_digest,
               'draft_digest': digest(draft), 'verified': False}
    save(path, receipt)
    state = bridge.call('check_stdraft_state', {'session_id': sid, 'local_package_id': ''})
    if state.get('server_package_id'):
        raise ValueError('新会话已有工程，请核对后恢复')
    receipt['status'] = 'push_pending'
    save(path, receipt)
    result = bridge.call('push_stdraft_snapshot', {'session_id': sid, 'base_package_id': '',
                                                'stdraft_json': json.dumps(draft, ensure_ascii=False)})
    if not result.get('server_package_id'):
        receipt.update(status='needs_attention', error_code=result.get('error_code', 'SAVE_UNCONFIRMED'))
        save(path, receipt)
        raise ValueError('CapCut 未确认保存；保留会话并在 Codex 处理，不能自动重推')
    raw = result.get('stdraft_json')
    if raw is None:
        snapshot = bridge.call('get_stdraft_snapshot', {'session_id': sid})
        raw = snapshot['stdraft_json']
        if snapshot['package_id'] != result['server_package_id']:
            raise ValueError('保存后已有新的人工作业版本，请在 Codex 核对')
    actual = json.loads(raw) if isinstance(raw, str) else raw
    save(path.with_name('cloud-baseline.json'), actual)
    errors = verify_roundtrip(draft, actual)
    receipt.update(status='ready' if not errors else 'needs_attention',
        verified=not errors, server_package_id=result['server_package_id'],
        web_editor_url=editor_url(result['web_editor_url']),
        embed_editor_url=editor_url(result['embed_editor_url']),
        web_editor_text=result['web_editor_text'], verification_errors=errors,
        counts={k: sum(s['type'] == k for s in tracks(actual)) for k in ('VIDEO', 'AUDIO', 'TEXT')},
        review='pending_human', limitations=['云端结构已核对；转场/特效的实际画面与声音仍需正常速度审看'])
    save(path, receipt)
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    for name in ('plan', 'media', 'builder-root', 'out'):
        b.add_argument('--' + name, type=Path, required=True)
    b.add_argument('--input', type=Path)
    b = sub.add_parser('publish-new')
    b.add_argument('--bundle', type=Path, required=True)
    b.add_argument('--receipt', type=Path, required=True)
    args = p.parse_args()
    if args.command == 'build':
        plan, media = json.loads(args.plan.read_text()), json.loads(args.media.read_text())
        if args.input:
            validate_inputs(plan, media, json.loads(args.input.read_text()))
        result = compile_plan(plan, media, args.builder_root)
        save(args.out, result)
        print(json.dumps({'draft_digest': digest(result['draft']), 'warnings': result['warnings']}, ensure_ascii=False))
    else:
        from capcut_bridge import CapCutBridge
        from cut_audit import validate_review
        bundle = json.loads(args.bundle.read_text())
        validate_review(json.loads((args.bundle.parent / 'edit-plan.json').read_text()),
                        json.loads((args.bundle.parent / 'cut-audit.json').read_text()))
        b = CapCutBridge(args.bundle.parent)
        try:
            print(json.dumps(publish_new(bundle['draft'], args.receipt, bundle['selection_digest'], b), ensure_ascii=False))
        finally:
            b.close()


if __name__ == '__main__':
    main()
