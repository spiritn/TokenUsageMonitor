"""适配层：只输出计量元数据，不保存提示词或工具参数。"""
import hashlib
from datetime import datetime
from pathlib import Path


def timestamp(value):
    if isinstance(value, (int, float)):
        return int(value if value > 1e11 else value * 1000)
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


def number(value):
    n = int(value or 0)
    if n < 0:
        raise ValueError('negative usage')
    return n


def identity(*values):
    return hashlib.sha256('\0'.join(str(x) for x in values).encode()).hexdigest()


def event(tool, session, response, ts, model, cwd, inp, out, cache=None, write=None, **extra):
    cache_known = cache is not None
    inp, out, cache, write = map(number, (inp, out, cache, write))
    if cache + write > inp:
        raise ValueError('cache exceeds normalized input')
    return dict(tool=tool, account='local', session=str(session), response_id=str(response),
                ts=timestamp(ts), model=model or 'unknown', cwd=cwd or '',
                input_tokens=inp, output_tokens=out, cache_read=cache, cache_write=write,
                provider='unknown', credits=None, basis='response', source='local_log', cache_known=int(cache_known),
                **extra)


class Claude:
    tool, name = 'claude', 'Claude Code'
    coverage = '本机主会话与子 Agent 日志；不代表完整账单'

    def paths(self, home):
        return (home / '.claude/projects').glob('**/*.jsonl')

    def parse(self, d, ctx, path):
        if d.get('cwd'):
            ctx['cwd'] = d['cwd']
        m = d.get('message')
        if d.get('type') != 'assistant' or not isinstance(m, dict) or not isinstance(m.get('usage'), dict):
            return []
        u = m['usage']
        if m.get('model') == '<synthetic>' or not m.get('id'):
            return []
        read, write = number(u.get('cache_read_input_tokens')), number(u.get('cache_creation_input_tokens'))
        e = event(self.tool, d.get('sessionId') or path.stem, m['id'], d['timestamp'],
                  m.get('model'), ctx.get('cwd'), number(u.get('input_tokens')) + read + write,
                  u.get('output_tokens'), read, write)
        # 响应 id 在父子日志中重复时保持同一个计量事件。
        model = e['model'].lower()
        e['provider'] = 'deepseek' if model.startswith('deepseek') else ('anthropic' if model.startswith('claude') else 'unknown')
        e['cache_known'] = int('cache_read_input_tokens' in u)
        return [e]


class WorkBuddy:
    tool, name = 'workbuddy', 'WorkBuddy'
    coverage = '本地日志 token 与积分；账户共享积分另行对账'

    def paths(self, home):
        return (home / '.workbuddy/projects').glob('**/*.jsonl')

    def parse(self, d, ctx, path):
        m, pd = d.get('message'), d.get('providerData') or {}
        if not isinstance(m, dict) or not isinstance(m.get('usage'), dict):
            return []
        u, raw = m['usage'], pd.get('rawUsage') or {}
        sid = d.get('sessionId') or path.stem
        rid = pd.get('messageId') or d.get('id')
        if not rid:
            return []
        inp, out = number(u.get('input_tokens')), number(u.get('output_tokens'))
        if u.get('total_tokens') is not None and number(u['total_tokens']) != inp + out:
            raise ValueError('WorkBuddy total usage schema changed')
        e = event(self.tool, sid, identity(sid, pd.get('conversationRequestId'), rid),
                  d['timestamp'], pd.get('requestModelName') or pd.get('model'), d.get('cwd'),
                  inp, out, u.get('cache_read_input_tokens'))
        if isinstance(raw.get('credit'), (int, float)) and raw['credit'] >= 0:
            e['credits'] = float(raw['credit'])
        e['provider'] = 'codebuddy'
        return [e]


class Codex:
    tool, name = 'codex', 'Codex'
    coverage = '本机 rollout；优先响应记录，旧格式累计差分'

    def paths(self, home):
        for name in ('sessions', 'archived_sessions'):
            yield from (home / '.codex' / name).glob('**/*.jsonl')

    def parse(self, d, ctx, path):
        p, t = d.get('payload') or {}, d.get('type')
        if t == 'session_meta':
            ctx.update(session=p.get('id') or p.get('session_id'), cwd=p.get('cwd'), provider=p.get('model_provider', 'openai'))
        elif t == 'turn_context':
            ctx.update(model=p.get('model'), cwd=p.get('cwd') or ctx.get('cwd'))
        sid = ctx.get('session') or path.stem
        if t == 'token_usage_record':
            u = p.get('usage') or {}
            if not p.get('response_id') or not u:
                return []
            # 后续 token_count 是同一用量的快照，不能再记一遍。
            pending = ctx.setdefault('pending', [0, 0, 0, 0])
            vals = [number(u.get(k)) for k in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens')]
            recent = ctx.setdefault('record_usage', {})
            previous = recent.get(p['response_id'], [0, 0, 0, 0])
            ctx['pending'] = [a+max(0,b-c) for a,b,c in zip(pending, vals, previous)]
            recent[p['response_id']] = vals
            if len(recent) > 256:
                del recent[next(iter(recent))]
            e = event(self.tool, p.get('session_id') or sid, p['response_id'], d['timestamp'],
                      ctx.get('model'), ctx.get('cwd'), *vals)
            e['provider'] = ctx.get('provider', 'openai')
            e['cache_known'] = int('cached_input_tokens' in u)
            # 累计快照可能先落盘。只有区间可对应，或同时间、同用量时才冲减快照。
            increment = [max(0, b-c) for b,c in zip(vals, previous)]
            corrections = []
            endpoint = p.get('thread_token_usage')
            endpoint = [number(endpoint.get(k)) for k in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens')] if isinstance(endpoint, dict) else None
            for snapshot in reversed(ctx.get('unmatched_snapshots', [])):
                remaining = snapshot['remaining']
                if not any(increment[:2]) or not all(a >= b for a,b in zip(remaining, increment)):
                    continue
                if endpoint:
                    matches = all(snapshot['start'][i] <= endpoint[i]-increment[i] <= endpoint[i] <= snapshot['end'][i] for i in (0,1))
                else:
                    matches = remaining == increment and snapshot['event']['ts'] == e['ts']
                if not matches:
                    continue
                remaining = [a-b for a,b in zip(remaining, increment)]
                # 无法保持缓存归一化时保留原记录，交由健康提示人工核查。
                if remaining[2]+remaining[3] > remaining[0]:
                    continue
                snapshot['remaining'] = remaining
                correction = dict(snapshot['event'])
                correction.update(zip(('input_tokens','output_tokens','cache_read','cache_write'), remaining))
                correction['_delete'] = not any(remaining[:2])
                corrections.append(correction)
                ctx['pending'] = [max(0,a-b) for a,b in zip(ctx['pending'],increment)]
                break
            ctx['unmatched_snapshots'] = [s for s in ctx.get('unmatched_snapshots', []) if any(s['remaining'][:2])]
            return corrections + [e]
        if t != 'event_msg' or p.get('type') != 'token_count':
            return []
        if p.get('rate_limits'):
            ctx['quota'] = {'tool': self.tool, 'ts': timestamp(d['timestamp']), 'data': p['rate_limits']}
        u = (p.get('info') or {}).get('total_token_usage')
        if not isinstance(u, dict):
            return []
        vals = [number(u.get(k)) for k in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens')]
        prev = ctx.get('total', [0, 0, 0, 0])
        if any(a < b for a,b in zip(vals, prev)):
            # 压缩/恢复导致累计值回退时，仅采本次明确的 last usage。
            last = (p.get('info') or {}).get('last_token_usage')
            ctx['epoch'] = ctx.get('epoch', 0) + 1
            ctx['unmatched_snapshots'] = []
            if not isinstance(last, dict):
                ctx['total'] = vals
                return []
            delta = [number(last.get(k)) for k in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens')]
        else:
            delta = [a-b for a,b in zip(vals, prev)]
        ctx['total'] = vals
        range_start = [a-b for a,b in zip(vals,delta)]
        pending = ctx.get('pending', [0, 0, 0, 0])
        ctx['pending'] = [max(0, b-a) for a,b in zip(delta, pending)]
        delta = [max(0, a-b) for a,b in zip(delta, pending)]
        if not delta[0] and not delta[1]:
            return []
        e = event(self.tool, sid, 'snapshot:'+identity(sid, ctx.get('epoch',0), vals), d['timestamp'],
                  ctx.get('model'), ctx.get('cwd'), *delta)
        e['basis'] = 'cumulative_delta'
        e['provider'] = ctx.get('provider', 'openai')
        e['cache_known'] = int('cached_input_tokens' in u)
        ctx.setdefault('unmatched_snapshots', []).append(dict(start=range_start, end=vals, remaining=delta, event=dict(e)))
        return [e]


class Qianwen:
    tool, name = 'qianwen', '千问 Agent'
    coverage = '仅桌面 Agent 模式的 canonical usage；不含普通聊天'

    def paths(self, home):
        return (home / 'Library/Application Support/Qianwen/qwen-agent').glob('*/projects/*/sessions/*/thread-events.jsonl')

    def parse(self, d, ctx, path):
        item = (d.get('payload') or {}).get('item') or {}
        u = item.get('usage')
        if d.get('type') != 'canonical_item' or item.get('kind') != 'usage' or item.get('usageSource') != 'provider' or not isinstance(u, dict) or not item.get('id'):
            return []
        sid = d.get('threadId') or path.parent.name
        read, write = number(u.get('cache_read_input_tokens')), number(u.get('cache_creation_input_tokens'))
        e = event(self.tool, sid, identity(sid, item.get('reqId') or item['id']), d['timestamp'],
                  item.get('model'), '', number(u.get('input_tokens')) + read + write,
                  u.get('output_tokens'), read, write)
        e['provider'] = 'qianwen'
        e['cache_known'] = int('cache_read_input_tokens' in u)
        return [e]


class Dsh:
    tool, name = 'dsh', 'DeepSeek Harness'
    coverage = '未压缩 session.jsonl；不自动修改宿主压缩配置'

    def paths(self, home):
        return (home / '.dsh/sessions').glob('**/session.jsonl')

    def parse(self, d, ctx, path):
        data = d.get('data') or {}
        if d.get('type') == 'session':
            ctx['cwd'] = data.get('cwd')
        if d.get('type') == 'request/context':
            ctx['model'] = data.get('model')
        if d.get('type') != 'assistant/message' or not isinstance(data.get('usage'),dict):
            return []
        u = data['usage'];read=number(u.get('cacheReadTokens'));write=number(u.get('cacheWriteTokens'))
        e = event(self.tool, d.get('session_id') or d.get('session') or path.parent.name,
                  identity(path.parent.name,d.get('seq')),d['time'],ctx.get('model'),ctx.get('cwd'),
                  number(u.get('inputTokens'))+read+write,u.get('outputTokens'),read,write)
        e['provider']='deepseek'
        e['cache_known'] = int('cacheReadTokens' in u)
        return [e]


ADAPTERS = [Claude(), Codex(), WorkBuddy(), Qianwen(), Dsh()]
UNAVAILABLE = [
    dict(tool='chatgpt', name='ChatGPT 聊天', status='unsupported', coverage='未发现经过验证的聊天 token 数据源；Codex 单独计量'),
    dict(tool='doubao', name='豆包聊天', status='unsupported', coverage='未发现经过验证的 token 日志；不以方舟 API 账单替代'),
]
