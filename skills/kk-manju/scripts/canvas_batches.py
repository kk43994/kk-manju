#!/usr/bin/env python3
"""Validate explicit LibTV batch membership and verify readback without generation."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def index(snapshot):
    nodes = snapshot.get('nodes', [])
    result = {n['id']: n for n in nodes}
    if len(result) != len(nodes):
        raise ValueError('快照存在重复节点 ID')
    return result


def preflight(snapshot, plan):
    if plan.get('projectId') != snapshot.get('projectId'):
        raise ValueError('计划与画布项目不一致')
    revision = snapshot.get('revision')
    revision = revision.get('token') if isinstance(revision, dict) else revision
    if plan.get('revision') and plan['revision'] != revision:
        raise ValueError('计划与整理前快照版本不一致')
    nodes, seen, keys, names = index(snapshot), set(), set(), set()
    for group in plan.get('groups', []):
        key, name, ids = group.get('key'), group.get('name'), group.get('nodes')
        if not key or key in keys or not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('批次标识或名称为空/重复')
        if not isinstance(ids, list) or not ids:
            raise ValueError('批次必须包含明确节点')
        for nid in ids:
            if nid not in nodes or nodes[nid].get('type') == 'group':
                raise ValueError('未知节点或嵌套分组: ' + str(nid))
            if nid in seen:
                raise ValueError('同一节点重复归组: ' + nid)
            seen.add(nid)
        if group.get('existing') and (group['existing'] not in nodes or nodes[group['existing']].get('type') != 'group'):
            raise ValueError('原有分组不存在')
        keys.add(key); names.add(name)
    return {'projectId': plan['projectId'], 'groups': len(keys), 'assignedNodes': len(seen),
            'unassigned': [n for n in nodes if nodes[n].get('type') != 'group' and n not in seen]}


def media_core(node):
    # Layout, selection and synchronization bookkeeping are deliberately excluded.
    data = {k: v for k, v in node.get('data', {}).items()
            if k not in ('_updatedAtMs', '_lastAppliedFullyAtMs', 'groupId', 'parentId')}
    return {'type': node.get('type'), 'data': data, 'resourceMeta': node.get('resourceMeta')}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def verify(before, after, plan, receipts):
    preflight(before, plan)
    if before['projectId'] != after.get('projectId'):
        raise ValueError('回读项目不一致')
    old, new = index(before), index(after)
    issues = []
    for nid, node in old.items():
        if nid not in new:
            issues.append('原节点丢失: ' + nid)
        elif node.get('type') != 'group' and media_core(node) != media_core(new[nid]):
            issues.append('节点内容发生变化: ' + nid)
    def edge_core(s):
        return sorted([{k: v for k, v in e.items() if k not in ('selected', 'style')}
                       for e in s.get('edges', [])], key=lambda e: e['id'])
    if edge_core(before) != edge_core(after):
        issues.append('连线定义发生变化')
    receipt_map = {r['key']: r['groupId'] for r in receipts}
    if len(receipt_map) != len(receipts):
        issues.append('回执批次重复')
    for group in plan['groups']:
        gid = receipt_map.get(group['key'])
        actual = new.get(gid, {})
        if actual.get('type') != 'group':
            issues.append('分组未回读: ' + group['key']); continue
        if actual.get('data', {}).get('name') != group['name']:
            issues.append('组名不一致: ' + group['key'])
        members = sorted(n['id'] for n in new.values() if n.get('parentId') == gid)
        if members != sorted(group['nodes']):
            issues.append('分组成员不一致: ' + group['key'])
        saved_members = actual.get('data', {}).get('childNodeIds')
        if saved_members is not None and sorted(saved_members) != sorted(group['nodes']):
            issues.append('持久化组内清单不一致: ' + group['key'])
    groups = [n for n in new.values() if n.get('type') == 'group']
    collisions = []
    for i, a in enumerate(groups):
        for b in groups[i+1:]:
            if not all(n.get('position') and n.get('width') and n.get('height') for n in (a,b)):
                continue
            ap,bp = a['position'],b['position']
            if (min(ap['x']+a['width'],bp['x']+b['width']) > max(ap['x'],bp['x'])+2
                    and min(ap['y']+a['height'],bp['y']+b['height']) > max(ap['y'],bp['y'])+2):
                collisions.append([a['id'],b['id']])
    if collisions:
        issues.append('分组边框存在重叠')
    batches = [{'id': g['id'], 'name': g.get('data', {}).get('name', ''),
                'count': sum(n.get('parentId') == g['id'] for n in new.values())}
               for g in groups]
    return {'ok': not issues, 'issues': issues, 'collisions': collisions, 'projectId': after['projectId'],
            'nodes': len(new), 'groups': len(groups), 'edges': len(after.get('edges', [])),
            'batches': batches, 'observedAt': datetime.now(timezone.utc).isoformat(),
            'beforeHash': fingerprint(before), 'afterHash': fingerprint(after),
            'revision': after.get('revision'), 'basis': 'libtv_read_project',
            'mediaReview': 'unchanged_not_reapproved'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'verify'])
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--after', type=Path)
    parser.add_argument('--receipts', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'verify' and (not args.after or not args.receipts):
        parser.error('verify 需要 --after 和 --receipts')
    result = (preflight(read(args.before), read(args.plan)) if args.action == 'preflight'
              else verify(read(args.before), read(args.after), read(args.plan), read(args.receipts)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('batches', 'unassigned')}, ensure_ascii=False))
    return 0 if result.get('ok', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
