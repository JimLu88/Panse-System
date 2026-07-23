(() => {
  'use strict';

  const CODE_RE = /^P(?:PS|FG)\d{8,}$/i;
  const listCache = new Map();
  let timer = 0;
  let objectUrls = [];

  const token = () => localStorage.getItem('panse_token') || '';
  const authHeaders = () => ({ Authorization: `Bearer ${token()}` });

  async function fetchJson(url) {
    const response = await fetch(url, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) throw new Error(`读取失败（${response.status}）`);
    return response.json();
  }

  function getAssetList(code) {
    if (!listCache.has(code)) {
      listCache.set(code, fetchJson(`/api/products/${encodeURIComponent(code)}/dimensions`));
    }
    return listCache.get(code);
  }

  function releaseImages() {
    objectUrls.forEach((url) => URL.revokeObjectURL(url));
    objectUrls = [];
  }

  function closeModal() {
    releaseImages();
    document.querySelector('.panse-final-dimension-modal')?.remove();
  }

  function openModal(title) {
    closeModal();
    const overlay = document.createElement('div');
    overlay.className = 'panse-final-dimension-modal';
    overlay.innerHTML = `
      <section class="panse-final-dimension-dialog" role="dialog" aria-modal="true">
        <header>
          <strong></strong>
          <button type="button" aria-label="关闭">×</button>
        </header>
        <main><div class="panse-final-dimension-loading">正在读取最终文件…</div></main>
      </section>`;
    overlay.querySelector('strong').textContent = title;
    overlay.querySelector('header button').addEventListener('click', closeModal);
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) closeModal();
    });
    document.body.appendChild(overlay);
    return overlay.querySelector('main');
  }

  async function loadDetails(code) {
    const listing = await getAssetList(code);
    const assets = listing.assets || [];
    const details = await Promise.all(assets.map((asset) => fetchJson(
      `/api/products/${encodeURIComponent(code)}/dimensions/${asset.id}`,
    )));
    return { product: listing.product || {}, details };
  }

  function showError(main, error) {
    main.replaceChildren();
    const message = document.createElement('div');
    message.className = 'panse-final-dimension-error';
    message.textContent = error instanceof Error ? error.message : '读取失败';
    main.appendChild(message);
  }

  async function showImages(code) {
    const main = openModal(`${code} · 最终尺寸图`);
    try {
      const { product, details } = await loadDetails(code);
      main.replaceChildren();
      for (const detail of details) {
        if (!detail.preview_url) continue;
        const response = await fetch(detail.preview_url, { headers: authHeaders(), cache: 'no-store' });
        if (!response.ok) throw new Error(`尺寸图读取失败（${response.status}）`);
        const url = URL.createObjectURL(await response.blob());
        objectUrls.push(url);
        const section = document.createElement('section');
        section.className = 'panse-final-dimension-image';
        const heading = document.createElement('h3');
        heading.textContent = details.length > 1 ? detail.title : (product.name || detail.title || code);
        const image = document.createElement('img');
        image.src = url;
        image.alt = `${detail.title || product.name || code}最终尺寸图`;
        section.append(heading, image);
        main.appendChild(section);
      }
      if (!main.children.length) throw new Error('该产品还没有最终尺寸图');
    } catch (error) {
      showError(main, error);
    }
  }

  function fallbackText(detail) {
    const confirmed = (detail.erp_dimensions || [])
      .map((item) => `${item.label || '尺寸'}：${item.value || ''}`)
      .filter(Boolean);
    return detail.size_detail || confirmed.join('\n') || '该产品还没有文字说明。';
  }

  async function showText(code) {
    const main = openModal(`${code} · 尺寸文字说明`);
    try {
      const { details } = await loadDetails(code);
      main.replaceChildren();
      details.forEach((detail) => {
        const section = document.createElement('section');
        section.className = 'panse-final-dimension-text';
        if (details.length > 1) {
          const heading = document.createElement('h3');
          heading.textContent = detail.title;
          section.appendChild(heading);
        }
        const text = document.createElement('pre');
        text.textContent = detail.dimension_data?.final_text || fallbackText(detail);
        section.appendChild(text);
        main.appendChild(section);
      });
      if (!main.children.length) throw new Error('该产品还没有文字说明');
    } catch (error) {
      showError(main, error);
    }
  }

  function actionButton(label, handler) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'panse-final-dimension-button ant-btn ant-btn-default ant-btn-sm';
    button.textContent = label;
    button.addEventListener('click', handler);
    return button;
  }

  async function ensureActions(code, host, mobile) {
    const selector = `.panse-final-dimension-actions[data-product-code="${code}"]`;
    if (host.querySelector(selector)) return;
    const actions = document.createElement('span');
    actions.className = `panse-final-dimension-actions${mobile ? ' is-mobile' : ''}`;
    actions.dataset.productCode = code;
    actions.hidden = true;
    host.appendChild(actions);
    try {
      const listing = await getAssetList(code);
      if (!document.body.contains(actions)) return;
      if (!(listing.assets || []).length) {
        actions.remove();
        return;
      }
      actions.append(
        actionButton('尺寸图', () => showImages(code)),
        actionButton('文字说明', () => showText(code)),
      );
      actions.hidden = false;
    } catch {
      actions.remove();
      listCache.delete(code);
    }
  }

  function inject() {
    if (location.pathname.replace(/\/+$/, '') !== '/products') return;
    document.querySelectorAll('code').forEach((codeNode) => {
      const code = (codeNode.textContent || '').trim();
      if (!CODE_RE.test(code)) return;
      const row = codeNode.closest('tr.ant-table-row');
      if (row) {
        const actionCell = row.querySelector('td:last-child');
        if (actionCell) ensureActions(code, actionCell, false);
        return;
      }
      if (codeNode.parentElement) ensureActions(code, codeNode.parentElement, true);
    });
  }

  function schedule() {
    clearTimeout(timer);
    timer = window.setTimeout(inject, 80);
  }

  const style = document.createElement('style');
  style.textContent = `
    .panse-final-dimension-actions { display: inline-flex; gap: 5px; margin-inline-start: 6px; vertical-align: middle; }
    .panse-final-dimension-actions.is-mobile { display: flex; margin: 6px 0 0; }
    .panse-final-dimension-button { color: #1677ff !important; border-color: #91caff !important; white-space: nowrap; }
    .panse-final-dimension-button:hover { color: #0958d9 !important; border-color: #1677ff !important; }
    .panse-final-dimension-modal { position: fixed; inset: 0; z-index: 99999; display: grid; place-items: center; padding: 18px; background: rgba(0,0,0,.58); }
    .panse-final-dimension-dialog { width: min(1000px, 96vw); max-height: 92vh; overflow: hidden; display: flex; flex-direction: column; background: #fff; border-radius: 12px; box-shadow: 0 18px 60px rgba(0,0,0,.3); }
    .panse-final-dimension-dialog > header { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid #eee; }
    .panse-final-dimension-dialog > header strong { font-size: 16px; }
    .panse-final-dimension-dialog > header button { width: 34px; height: 34px; border: 0; background: transparent; cursor: pointer; font-size: 27px; line-height: 1; color: #666; }
    .panse-final-dimension-dialog > main { overflow: auto; padding: 18px; background: #f5f5f5; }
    .panse-final-dimension-loading, .panse-final-dimension-error { padding: 50px 12px; text-align: center; color: #666; }
    .panse-final-dimension-error { color: #cf1322; }
    .panse-final-dimension-image, .panse-final-dimension-text { margin: 0 0 16px; padding: 14px; background: #fff; border-radius: 8px; }
    .panse-final-dimension-image:last-child, .panse-final-dimension-text:last-child { margin-bottom: 0; }
    .panse-final-dimension-image h3, .panse-final-dimension-text h3 { margin: 0 0 10px; font-size: 14px; }
    .panse-final-dimension-image img { display: block; width: 100%; height: auto; max-height: 72vh; object-fit: contain; }
    .panse-final-dimension-text pre { margin: 0; white-space: pre-wrap; word-break: break-word; font: 14px/1.75 system-ui, sans-serif; color: #262626; }
    @media (max-width: 640px) {
      .panse-final-dimension-modal { padding: 8px; }
      .panse-final-dimension-dialog { width: 100%; max-height: 95vh; }
      .panse-final-dimension-dialog > main { padding: 10px; }
    }
  `;
  document.head.appendChild(style);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeModal();
  });
  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
  window.addEventListener('popstate', schedule);
  const originalPush = history.pushState;
  history.pushState = function (...args) { originalPush.apply(this, args); schedule(); };
  const originalReplace = history.replaceState;
  history.replaceState = function (...args) { originalReplace.apply(this, args); schedule(); };
  schedule();
})();
