# -*- coding: utf-8 -*-
"""
飞书导出 HTML -> Markdown

用法: python feishu_html2md.py <in.html> [out.md]
依赖: beautifulsoup4 + lxml
支持块: heading1-3 / text / bullet / ordered / code / divider / callout / table
"""
import sys, re, os
from bs4 import BeautifulSoup, Tag

ZERO = '​'
LANG_FIX = {'plain text': 'text', 'plaintext': 'text', '': ''}


def is_bold(el):
    while isinstance(el, Tag):
        if 'font-weight:bold' in (el.get('style') or '').replace(' ', ''):
            return True
        el = el.parent
    return False


def inline(node):
    """把一个 ace-line 渲染成 markdown 行"""
    out = []
    for sp in node.find_all('span', attrs={'data-leaf': 'true'}):
        if sp.get('data-enter') == 'true' or sp.find_parent('span', attrs={'data-leaf': 'true'}):
            continue
        code = sp.find('span', class_='inline-code')
        if code:
            t = code.get_text().replace(ZERO, '')
            if t.strip():
                out.append('`%s`' % t)
            continue
        t = sp.get_text().replace(ZERO, '')
        if not t:
            continue
        if is_bold(sp) and t.strip():
            lead = t[:len(t) - len(t.lstrip())]
            trail = t[len(t.rstrip()):]
            t = '%s**%s**%s' % (lead, t.strip(), trail)
        out.append(t)
    return ''.join(out).rstrip()


def lines(block, sep='\n'):
    zone = block.select_one('.zone-container')
    if zone is None:
        return ''
    return sep.join(inline(l) for l in zone.select('.ace-line'))


def depth_of(block):
    """嵌套层级 = 祖先中 .list-children 的个数"""
    return len(block.find_parents(class_='list-children'))


def cell_md(td):
    parts = [lines(b, sep='<br>') for b in td.select('[data-block-type="text"]')]
    return '<br>'.join(p for p in parts if p).replace('|', chr(92) + '|')


def table_md(tbl):
    rows = [[cell_md(td) for td in tr.find_all('td')] for tr in tbl.find_all('tr')]
    if not rows:
        return ''
    ncol = max(len(r) for r in rows)
    rows = [r + [''] * (ncol - len(r)) for r in rows]
    md = ['| ' + ' | '.join(rows[0]) + ' |',
          '| ' + ' | '.join(['---'] * ncol) + ' |']
    md += ['| ' + ' | '.join(r) + ' |' for r in rows[1:]]
    return '\n'.join(md)


def code_md(block):
    btn = block.select_one('.code-block-header-btn span')
    lang = btn.get_text().strip().lower() if btn else ''
    lang = LANG_FIX.get(lang, lang)
    content = block.select_one('.code-block-content')
    body = '\n'.join(l.get_text().replace(ZERO, '') for l in content.select('.ace-line')) if content else ''
    return '```%s\n%s\n```' % (lang, body.rstrip())


def blocks_to_md(soup):
    out = []
    h1 = soup.body.find('h1', recursive=False)
    if h1:
        out.append('# ' + ' '.join(h1.get_text().split()))
    for el in soup.body.select('[data-block-type]'):
        bt = el.get('data-block-type')
        if bt == 'table_cell':
            continue
        # 表格单元格 / 代码块内部的子块由所属块自己渲染
        if any(p.get('data-block-type') in ('table', 'code', 'callout') for p in el.find_parents(attrs={'data-block-type': True})):
            continue
        pad = '  ' * depth_of(el)
        if bt in ('heading1', 'heading2', 'heading3'):
            out.append('#' * int(bt[-1]) + ' ' + lines(el, sep=' '))
        elif bt == 'text':
            t = lines(el)
            if t.strip():
                out.append('\n'.join(pad + l for l in t.split('\n')))
        elif bt == 'bullet':
            out.append(pad + '- ' + lines(el, sep=' '))
        elif bt == 'ordered':
            mark = el.select_one('.order')
            out.append(pad + (mark.get_text().strip() if mark else '1.') + ' ' + lines(el, sep=' '))
        elif bt == 'code':
            out.append(code_md(el))
        elif bt == 'divider':
            out.append('---')
        elif bt == 'callout':
            out.append('\n'.join('> ' + l for l in lines(el).split('\n')))
        elif bt == 'table':
            out.append(table_md(el))
    return out


def convert(path):
    soup = BeautifulSoup(open(path, encoding='utf-8').read(), 'lxml')
    chunks = blocks_to_md(soup)
    is_item = [bool(re.match(r'^\s*(-|\d+\.)\s', c)) for c in chunks]
    md = ''
    for i, c in enumerate(chunks):
        md += c
        if i + 1 < len(chunks):
            md += '\n' if (is_item[i] and is_item[i + 1]) else '\n\n'
    return re.sub(r'\n{3,}', '\n\n', md).strip() + '\n'


if __name__ == '__main__':
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + '.md'
    open(dst, 'w', encoding='utf-8', newline='\n').write(convert(src))
    print('->', dst)
