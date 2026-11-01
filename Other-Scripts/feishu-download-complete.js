// 飞书只读文档保存到本地（记得改下文件名）。
// 下载当前飞书文档的全部正文块（不含左侧/文内目录）。
// 用法：打开文档，F12 -> Console，粘贴整个文件并回车。
(async () => {
  const CONFIG = {
    settleMs: 350,
    stepRatio: 0.65,
    bottomPasses: 5,
    maxSteps: 4000,
  };

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const escapeHtml = value => String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const pageRoot = document.querySelector(
    '.docx-page-block[data-block-type="page"]'
  );
  const pageChildren = pageRoot?.querySelector('.page-block-children');
  const scroller = pageRoot?.closest('.bear-web-x-container');

  if (!pageRoot || !pageChildren || !scroller) {
    throw new Error('未找到飞书正文。请确认当前页面已打开文档并完成加载。');
  }

  const rootBlockId = pageRoot.getAttribute('data-block-id');
  const originalTop = scroller.scrollTop;
  const blocks = new Map();
  const tables = new Map();
  let sequence = 0;

  const serializeBlock = element => {
    const clone = element.cloneNode(true);
    const liveNodes = [element, ...element.querySelectorAll('*')];
    const clonedNodes = [clone, ...clone.querySelectorAll('*')];

    for (let i = 0; i < liveNodes.length; i++) {
      const live = liveNodes[i];
      const copy = clonedNodes[i];
      let imageSourceCopied = false;

      if (live instanceof HTMLImageElement && live.currentSrc) {
        copy.setAttribute('src', live.currentSrc);
        copy.removeAttribute('srcset');
        imageSourceCopied = true;
      }

      for (const attribute of ['src', 'href', 'poster']) {
        if (attribute === 'src' && imageSourceCopied) continue;
        const value = live.getAttribute(attribute);
        if (!value || value.startsWith('#') || value.startsWith('data:') ||
            value.startsWith('blob:') || value.startsWith('javascript:')) {
          continue;
        }
        try {
          copy.setAttribute(attribute, new URL(value, document.baseURI).href);
        } catch {
          // 保留原属性值。
        }
      }
    }

    return clone.outerHTML;
  };

  const collectTable = (element, key, top) => {
    let table = tables.get(key);
    if (!table) {
      table = {
        blockId: element.getAttribute('data-block-id'),
        recordId: element.getAttribute('data-record-id'),
        top,
        sequence: sequence++,
        rows: new Map(),
      };
      tables.set(key, table);
    }

    for (const rowElement of element.querySelectorAll('tr[data-index]')) {
      const index = Number(rowElement.getAttribute('data-index'));
      let row = table.rows.get(index);
      if (!row) {
        row = { index, cells: new Map() };
        table.rows.set(index, row);
      }

      [...rowElement.children]
        .filter(cell => cell.matches('td,th'))
        .forEach((cell, column) => {
          const cellKey = cell.getAttribute('data-record-id') ||
            cell.getAttribute('data-block-id') || String(column);
          const old = row.cells.get(cellKey);
          const loaded = Boolean(cell.querySelector('.render-unit-wrapper')) &&
            !cell.querySelector('.bear-virtual-renderUnit-placeholder');

          row.cells.set(cellKey, {
            column,
            blockId: cell.getAttribute('data-block-id'),
            loaded: old?.loaded || loaded,
            text: loaded
              ? (cell.innerText || '').replaceAll('\u200B', '').trim()
              : old?.text || '',
            html: loaded ? serializeBlock(cell) : old?.html,
          });
        });
    }
  };

  const collect = () => {
    const scrollerRect = scroller.getBoundingClientRect();

    for (const element of pageChildren.querySelectorAll(
      '.block[data-block-id]'
    )) {
      const parentBlock = element.parentElement?.closest(
        '.block[data-block-id]'
      );

      // 只保存根页面的直接正文块；嵌套块已包含在父块 outerHTML 中。
      if (parentBlock?.getAttribute('data-block-id') !== rootBlockId) continue;

      const key = element.getAttribute('data-record-id') ||
        element.getAttribute('data-block-id');
      const rect = element.getBoundingClientRect();
      const old = blocks.get(key);
      const top = rect.top - scrollerRect.top + scroller.scrollTop;

      if (element.getAttribute('data-block-type') === 'table') {
        collectTable(element, key, top);
        continue;
      }

      blocks.set(key, {
        blockId: element.getAttribute('data-block-id'),
        type: element.getAttribute('data-block-type') || '',
        top,
        sequence: old?.sequence ?? sequence++,
        text: (element.innerText || '').replaceAll('\u200B', '').trim(),
        html: serializeBlock(element),
      });
    }
  };

  try {
    scroller.scrollTo(0, 0);
    await sleep(CONFIG.settleMs + 150);
    collect();

    let passesAtBottom = 0;
    for (let step = 0; step < CONFIG.maxSteps; step++) {
      const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      const nextTop = Math.min(
        maxTop,
        scroller.scrollTop + Math.max(250, scroller.clientHeight * CONFIG.stepRatio)
      );

      scroller.scrollTo(0, nextTop);
      await sleep(CONFIG.settleMs);
      collect();

      const atBottom = scroller.scrollTop >=
        scroller.scrollHeight - scroller.clientHeight - 2;
      passesAtBottom = atBottom ? passesAtBottom + 1 : 0;
      if (passesAtBottom >= CONFIG.bottomPasses) break;

      if (step === CONFIG.maxSteps - 1) {
        throw new Error('正文过长，达到最大滚动次数；请增大 CONFIG.maxSteps 后重试。');
      }
    }
  } finally {
    scroller.scrollTo(0, originalTop);
  }

  if (!blocks.size && !tables.size) throw new Error('没有采集到任何正文块。');

  const tableBlocks = [...tables.values()].map(table => {
    const rows = [...table.rows.values()].sort((a, b) => a.index - b.index);
    const cells = rows.flatMap(row => [...row.cells.values()]);
    const missing = cells.filter(cell => !cell.loaded);
    if (missing.length) {
      throw new Error(
        `表格 ${table.blockId} 仍有 ${missing.length} 个单元格未加载，请重试。`
      );
    }

    return {
      blockId: table.blockId,
      type: 'table',
      top: table.top,
      sequence: table.sequence,
      text: cells.map(cell => cell.text).join('\n'),
      html: `<table data-block-type="table" data-block-id="${escapeHtml(table.blockId)}"><tbody>${rows
        .map(row => `<tr data-index="${row.index}">${[...row.cells.values()]
          .sort((a, b) => a.column - b.column)
          .map(cell => cell.html)
          .join('')}</tr>`)
        .join('')}</tbody></table>`,
      rows: rows.length,
      cells: cells.length,
    };
  });

  const ordered = [...blocks.values(), ...tableBlocks].sort(
    (a, b) => a.top - b.top || a.sequence - b.sequence
  );
  const title = (
    pageRoot.querySelector('.page-block-header h1')?.innerText ||
    document.title ||
    '飞书文档'
  ).replaceAll('\u200B', '').trim();
  const filename = title
    .replace(/[\u200B-\u200F\u202A-\u202E\u2060\uFEFF]/g, '')
    .replace(/\s*-\s*飞书云文档\s*$/i, '')
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '')
    .trim() + '.html';
  const textChars = ordered.reduce((total, block) => total + block.text.length, 0);
  const tableRows = tableBlocks.reduce((total, table) => total + table.rows, 0);
  const tableCells = tableBlocks.reduce((total, table) => total + table.cells, 0);

  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <base href="${escapeHtml(location.href)}">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>${escapeHtml(title)}</title>
  <style>
    body{max-width:960px;margin:32px auto;padding:0 24px;color:#1f2329;font:16px/1.65 system-ui,sans-serif}
    img,video{max-width:100%;height:auto} table{border-collapse:collapse} td,th{border:1px solid #bbb;padding:6px}
    pre,.docx-code-block{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f7;padding:12px;margin:12px 0}
    [data-block-type]{margin:8px 0}
  </style>
</head>
<body>
<h1>${escapeHtml(title)}</h1>
<!-- Feishu body blocks: ${ordered.length}; text characters: ${textChars} -->
${ordered.map(block => block.html).join('\n')}
</body>
</html>`;

  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  document.getElementById('__feishu_complete_download_frame')?.remove();
  if (window.__feishuCompleteDownloadUrl) {
    URL.revokeObjectURL(window.__feishuCompleteDownloadUrl);
  }
  window.__feishuCompleteDownloadUrl = url;

  const frame = document.createElement('iframe');
  frame.id = '__feishu_complete_download_frame';
  Object.assign(frame.style, {
    position: 'fixed',
    right: '24px',
    bottom: '24px',
    zIndex: '2147483647',
    width: '236px',
    height: '58px',
    border: '0',
    background: 'transparent',
  });
  document.body.appendChild(frame);

  const frameDocument = frame.contentDocument;
  frameDocument.open();
  frameDocument.write(`<!doctype html><meta charset="utf-8">
    <style>
      html,body{margin:0;background:transparent}
      a{display:block;padding:13px 16px;border-radius:9px;background:#3370ff;color:#fff;font:16px/28px system-ui;text-align:center;text-decoration:none;box-shadow:0 4px 16px #0003}
    </style>
    <a id="download">点击下载完整正文 HTML</a>`);
  frameDocument.close();

  const link = frameDocument.getElementById('download');
  link.href = url;
  link.download = filename;
  link.addEventListener('click', () => {
    setTimeout(() => frame.remove(), 1000);
    setTimeout(() => {
      URL.revokeObjectURL(url);
      if (window.__feishuCompleteDownloadUrl === url) {
        delete window.__feishuCompleteDownloadUrl;
      }
    }, 60_000);
  }, { once: true });

  const result = {
    filename,
    blocks: ordered.length,
    textChars,
    bytes: blob.size,
    tables: tableBlocks.length,
    tableRows,
    tableCells,
    ready: true,
  };
  console.log('正文已准备好，请点击页面右下角的下载按钮：', result);
  return result;
})().catch(error => {
  console.error('飞书正文下载失败：', error);
  throw error;
});

