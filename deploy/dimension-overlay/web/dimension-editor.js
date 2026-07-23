"use strict";

const NS = "http://www.w3.org/2000/svg";
const state = {
  catalog: [],
  current: null,
  productCode: new URLSearchParams(location.search).get("product") || "",
  svg: null,
  selected: null,
  tool: "select",
  dimensionStart: null,
  drag: null,
  zoom: 1,
};

const $ = (id) => document.getElementById(id);
const productSelect = $("productSelect");
const artboard = $("artboard");

function setStatus(message) { $("statusText").textContent = message; }
function svgElement(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

async function jsonFetch(url, options) {
  const response = await authFetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
  return data;
}

function authFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = localStorage.getItem("panse_token");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...options, headers });
}

async function loadCatalog(preferredFile = null) {
  if (!state.productCode) throw new Error("链接缺少产品编码");
  const data = await jsonFetch(`/api/products/${encodeURIComponent(state.productCode)}/dimensions`);
  state.catalog = data.assets.map((item) => ({
    ...item,
    file: String(item.id),
    name: item.title,
  }));
  document.title = `细节尺寸 · ${data.product.name}`;
  $("pageTitle").textContent = `细节尺寸 · ${data.product.name}`;
  $("productCodeLabel").textContent = state.productCode;
  $("assetPath").textContent = "保存位置：群晖 ERP / storage / product_dimensions";
  productSelect.replaceChildren(...state.catalog.map((item) => {
    const option = document.createElement("option");
    option.value = item.file;
    option.textContent = `${item.name}（${item.dimension_count}项${item.mapping_status === "review_required" ? " · 待核对" : ""}）`;
    return option;
  }));
  if (!state.catalog.length) {
    setStatus("该产品暂时没有细节尺寸图，请返回产品表选择已绑定的产品。 ");
    $("saveButton").disabled = true;
    return;
  }
  const target = state.catalog.find((item) => item.file === String(preferredFile))
    || state.catalog.find((item) => item.is_primary)
    || state.catalog[0];
  productSelect.value = target.file;
  await loadAsset(target.file);
}

function sanitizeSvg(documentNode) {
  documentNode.querySelectorAll("script, foreignObject").forEach((node) => node.remove());
  documentNode.querySelectorAll("*").forEach((node) => {
    [...node.attributes].forEach((attr) => {
      if (/^on/i.test(attr.name)) node.removeAttribute(attr.name);
    });
  });
}

async function loadAsset(filename) {
  setStatus(`正在载入 ${filename}...`);
  const info = await jsonFetch(
    `/api/products/${encodeURIComponent(state.productCode)}/dimensions/${encodeURIComponent(filename)}`
  );
  const response = await authFetch(
    `/api/products/${encodeURIComponent(state.productCode)}/dimensions/${encodeURIComponent(filename)}/svg?v=${info.version}`
  );
  if (!response.ok) throw new Error(`SVG 载入失败：${response.status}`);
  const text = await response.text();
  const parsed = new DOMParser().parseFromString(text, "image/svg+xml");
  if (parsed.querySelector("parsererror")) throw new Error("SVG 解析失败");
  sanitizeSvg(parsed);
  const imported = document.importNode(parsed.documentElement, true);
  artboard.replaceChildren(imported);
  state.svg = imported;
  const summary = state.catalog.find((item) => item.file === String(filename));
  state.current = {
    ...summary,
    ...info,
    file: String(filename),
    name: info.title,
    erp: {
      erp_name: info.product_name,
      erp_code: info.product_code,
      review_required: info.mapping_status === "review_required",
      dimensions: info.erp_dimensions || [],
      variants: info.sku_variants || [],
      variant_summary: {
        sku_count: (info.sku_variants || []).length,
        resolved_dimension_count: (info.sku_variants || []).filter((v) => (v.resolved_dimensions || []).length).length,
      },
    },
  };
  $("erpSizeDetail").value = info.size_detail || "";
  $("confirmMapping").checked = info.mapping_status === "confirmed";
  $("mappingWarning").hidden = info.mapping_status !== "review_required";
  state.dimensionStart = null;
  selectElement(null);
  bindSvgEvents();
  renderDimensions();
  renderErp();
  fitCanvas();
  setStatus(`${state.current.name} · v${state.current.version} 已载入，可直接改尺寸或新增标注。`);
}

function getViewBox() {
  const values = (state.svg.getAttribute("viewBox") || "0 0 1000 1000").split(/[ ,]+/).map(Number);
  return { x: values[0], y: values[1], width: values[2], height: values[3] };
}

function applyZoom() {
  if (!state.svg) return;
  const box = getViewBox();
  state.svg.style.width = `${Math.round(box.width * state.zoom)}px`;
  state.svg.style.height = `${Math.round(box.height * state.zoom)}px`;
  $("zoomLabel").textContent = `${Math.round(state.zoom * 100)}%`;
}

function fitCanvas() {
  if (!state.svg) return;
  const box = getViewBox();
  const scroller = $("canvasScroller");
  state.zoom = Math.min((scroller.clientWidth - 70) / box.width, (scroller.clientHeight - 70) / box.height, 1.5);
  state.zoom = Math.max(0.15, state.zoom);
  applyZoom();
}

function svgPoint(event) {
  const point = state.svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(state.svg.getScreenCTM().inverse());
}

function ensureDimensionGroup() {
  let group = state.svg.querySelector("#dimensions-editable");
  if (!group) {
    group = svgElement("g", { id: "dimensions-editable", "data-layer-name": "尺寸-可编辑" });
    state.svg.appendChild(group);
  }
  return group;
}

function setTextGeometry(text, x, y, angle) {
  text.setAttribute("x", x.toFixed(2));
  text.setAttribute("y", y.toFixed(2));
  text.dataset.angle = String(angle);
  text.setAttribute("transform", `rotate(${angle.toFixed(2)},${x.toFixed(2)},${y.toFixed(2)})`);
}

function textGeometry(text) {
  const x = Number(text.getAttribute("x") || 0);
  const y = Number(text.getAttribute("y") || 0);
  const match = (text.getAttribute("transform") || "").match(/rotate\(\s*([-\d.]+)/i);
  const angle = Number(text.dataset.angle ?? (match ? match[1] : 0));
  return { x, y, angle };
}

function addDimension(start, end) {
  const box = getViewBox();
  const diag = Math.hypot(box.width, box.height);
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy);
  if (length < diag * 0.012) {
    setStatus("两点距离太短，请重新点击起点和终点。");
    return;
  }
  const ux = dx / length, uy = dy / length;
  const px = -uy, py = ux;
  const tick = diag * 0.011;
  const labelOffset = diag * 0.018;
  const stroke = Math.max(1, diag * 0.0012);
  const id = `dimension-manual-${Date.now()}`;
  const group = svgElement("g", { id, "data-editor-dimension": "manual" });
  group.append(
    svgElement("line", { x1: start.x, y1: start.y, x2: end.x, y2: end.y, stroke: "#292724", "stroke-width": stroke, "vector-effect": "non-scaling-stroke" }),
    svgElement("line", { x1: start.x - px * tick, y1: start.y - py * tick, x2: start.x + px * tick, y2: start.y + py * tick, stroke: "#292724", "stroke-width": stroke, "vector-effect": "non-scaling-stroke" }),
    svgElement("line", { x1: end.x - px * tick, y1: end.y - py * tick, x2: end.x + px * tick, y2: end.y + py * tick, stroke: "#292724", "stroke-width": stroke, "vector-effect": "non-scaling-stroke" })
  );
  let angle = Math.atan2(dy, dx) * 180 / Math.PI;
  if (angle > 90 || angle < -90) angle += 180;
  const x = (start.x + end.x) / 2 + px * labelOffset;
  const y = (start.y + end.y) / 2 + py * labelOffset;
  const text = svgElement("text", {
    id: `dim-text-manual-${Date.now()}`,
    "text-anchor": "middle",
    "dominant-baseline": "central",
    "font-family": "Microsoft YaHei, sans-serif",
    "font-size": Math.max(16, diag * 0.018),
    "font-weight": "700",
    fill: "#292724",
  });
  text.textContent = $("newDimensionValue").value.trim() || "1000";
  setTextGeometry(text, x, y, angle);
  group.appendChild(text);
  ensureDimensionGroup().appendChild(group);
  renderDimensions();
  selectElement(text);
  setTool("select");
  setStatus("新尺寸已加入；可拖动，并在左侧修改数值。 ");
}

function selectableTarget(eventTarget) {
  const manual = eventTarget.closest?.("[data-editor-dimension]");
  if (manual && state.svg.contains(manual)) return manual;
  const item = eventTarget.closest?.(
    "#dimensions-editable text:not([data-panel-static]), " +
    "#dimensions-editable path:not([data-panel-static]), " +
    "#dimensions-editable line:not([data-panel-static])"
  );
  return item && state.svg.contains(item) ? item : null;
}

function bindSvgEvents() {
  state.svg.addEventListener("pointerdown", (event) => {
    if (state.tool === "dimension") {
      const point = svgPoint(event);
      if (!state.dimensionStart) {
        state.dimensionStart = point;
        setStatus("已选起点，请再点击尺寸线终点。 ");
      } else {
        const start = state.dimensionStart;
        state.dimensionStart = null;
        addDimension(start, point);
      }
      event.preventDefault();
      return;
    }
    const target = selectableTarget(event.target);
    selectElement(target);
    if (!target) return;
    state.drag = { target, start: svgPoint(event), dx: 0, dy: 0 };
    target.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  });
  state.svg.addEventListener("pointermove", (event) => {
    if (!state.drag) return;
    const point = svgPoint(event);
    const dx = point.x - state.drag.start.x;
    const dy = point.y - state.drag.start.y;
    moveElement(state.drag.target, dx - state.drag.dx, dy - state.drag.dy);
    state.drag.dx = dx;
    state.drag.dy = dy;
    updateSelectionFields();
  });
  const endDrag = () => { if (state.drag) { state.drag = null; renderDimensions(); } };
  state.svg.addEventListener("pointerup", endDrag);
  state.svg.addEventListener("pointercancel", endDrag);
}

function moveElement(element, dx, dy) {
  if (element.tagName.toLowerCase() === "text") {
    const geo = textGeometry(element);
    setTextGeometry(element, geo.x + dx, geo.y + dy, geo.angle);
    return;
  }
  const tx = Number(element.dataset.editorX || 0) + dx;
  const ty = Number(element.dataset.editorY || 0) + dy;
  if (!element.dataset.baseTransform) element.dataset.baseTransform = element.getAttribute("transform") || "";
  element.dataset.editorX = String(tx);
  element.dataset.editorY = String(ty);
  element.setAttribute("transform", `translate(${tx.toFixed(2)} ${ty.toFixed(2)}) ${element.dataset.baseTransform}`.trim());
}

function selectElement(element) {
  state.selected?.classList.remove("editor-selected");
  state.selected = element;
  state.selected?.classList.add("editor-selected");
  updateSelectionFields();
  document.querySelectorAll(".dimension-row").forEach((row) => {
    row.classList.toggle("selected", !!element && row.dataset.id === (element.id || element.querySelector?.("text")?.id));
  });
}

function selectedText() {
  if (!state.selected) return null;
  if (state.selected.tagName.toLowerCase() === "text") return state.selected;
  return state.selected.querySelector?.("text") || null;
}

function updateSelectionFields() {
  const text = selectedText();
  $("emptySelection").hidden = !!state.selected;
  $("selectionFields").hidden = !state.selected;
  if (!state.selected) return;
  const fields = [$("selectedValue"), $("selectedX"), $("selectedY"), $("selectedAngle")];
  fields.forEach((field) => { field.disabled = !text; });
  if (!text) return;
  const geo = textGeometry(text);
  $("selectedValue").value = text.textContent;
  $("selectedX").value = Math.round(geo.x * 100) / 100;
  $("selectedY").value = Math.round(geo.y * 100) / 100;
  $("selectedAngle").value = Math.round(geo.angle * 100) / 100;
}

function dimensionTexts() {
  return state.svg
    ? [...state.svg.querySelectorAll("#dimensions-editable text:not([data-panel-static])")]
    : [];
}

function renderDimensions() {
  const list = $("dimensionList");
  const texts = dimensionTexts();
  $("dimensionCount").textContent = String(texts.length);
  list.replaceChildren(...texts.map((text, index) => {
    if (!text.id) text.id = `dim-text-editor-${Date.now()}-${index}`;
    const row = document.createElement("label");
    row.className = "dimension-row";
    row.dataset.id = text.id;
    const number = document.createElement("span");
    number.textContent = String(index + 1).padStart(2, "0");
    const input = document.createElement("input");
    input.value = text.textContent;
    input.addEventListener("focus", () => selectElement(text));
    input.addEventListener("input", () => {
      text.textContent = input.value;
      if (selectedText() === text) $("selectedValue").value = input.value;
    });
    row.addEventListener("click", () => selectElement(text));
    row.append(number, input);
    return row;
  }));
}

function renderErp() {
  const erp = state.current?.erp;
  const root = $("erpInfo");
  if (!erp) {
    $("erpStatus").textContent = "无匹配";
    root.className = "erp-info muted";
    root.textContent = "此图暂未匹配到 ERP 产品。";
    return;
  }
  $("erpStatus").textContent = erp.review_required ? "待核对" : "已匹配";
  root.className = "erp-info";
  const name = document.createElement("div");
  name.className = "erp-name";
  name.textContent = `${erp.erp_name || ""} · ${erp.erp_code || ""}`;
  const dimensions = document.createElement("div");
  dimensions.className = "muted";
  dimensions.textContent = (erp.dimensions || []).map((item) => `${item.label}：${item.value}`).join("；") || "ERP 暂无尺寸详情";
  root.replaceChildren(name, dimensions);
  const summary = erp.variant_summary || {};
  if (summary.sku_count) {
    const coverage = document.createElement("div");
    coverage.className = "erp-coverage";
    coverage.textContent = `SKU 可用尺寸：${summary.resolved_dimension_count || 0}/${summary.sku_count}；规格名直接解析 ${summary.parsed_dimension_count || 0} 个`;
    root.appendChild(coverage);
  }
  if (erp.review_required) {
    const warning = document.createElement("div");
    warning.className = "review-warning muted";
    warning.textContent = "这条映射为候选关系，应用规格前需要人工确认。";
    root.appendChild(warning);
  }
  (erp.variants || []).forEach((variant) => {
    const item = document.createElement("div");
    item.className = "erp-variant";
    const measurements = (variant.resolved_dimensions || variant.measurements || [])
      .map((measurement) => `${measurement.label}${measurement.value_mm}mm`)
      .join(" / ");
    item.textContent = measurements ? `${variant.name} · ${measurements}` : `${variant.name} · 规格名无显式尺寸`;
    root.appendChild(item);
  });
}

function setTool(tool) {
  state.tool = tool;
  state.dimensionStart = null;
  $("selectTool").classList.toggle("active", tool === "select");
  $("dimensionTool").classList.toggle("active", tool === "dimension");
  state.svg?.classList.toggle("dimension-tool", tool === "dimension");
  $("toolHint").textContent = tool === "dimension"
    ? "依次点击尺寸线的起点和终点，系统会自动生成线、端点和文字。"
    : "点击尺寸文字或线条后拖动；左侧可精确改值。";
}

function serializeSvg() {
  const clone = state.svg.cloneNode(true);
  clone.classList.remove("dimension-tool");
  clone.querySelectorAll(".editor-selected").forEach((node) => node.classList.remove("editor-selected"));
  clone.removeAttribute("style");
  clone.querySelectorAll("*").forEach((node) => {
    [...node.attributes].forEach((attr) => {
      if (attr.name.startsWith("data-editor-") && attr.name !== "data-editor-dimension") node.removeAttribute(attr.name);
    });
  });
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(clone)}\n`;
}

async function saveCurrent() {
  if (!state.current || !state.svg) return;
  $("saveButton").disabled = true;
  try {
    if (state.current.mapping_status === "review_required" && !$("confirmMapping").checked) {
      throw new Error("请先确认这张图确实属于当前 ERP 产品");
    }
    const result = await jsonFetch(
      `/api/products/${encodeURIComponent(state.productCode)}/dimensions/${state.current.id}`,
      {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        svg: serializeSvg(),
        expected_version: state.current.version,
        size_detail: $("erpSizeDetail").value.trim() || null,
        sync_size_detail: $("syncSizeDetail").checked,
        confirm_mapping: $("confirmMapping").checked,
      }),
    });
    state.current.version = result.version;
    state.current.mapping_status = result.mapping_status;
    state.current.erp.review_required = result.mapping_status === "review_required";
    $("mappingWarning").hidden = result.mapping_status !== "review_required";
    setStatus(result.backup
      ? `保存成功 · 当前 v${result.version}；原版本已自动备份`
      : `保存成功 · 当前 v${result.version}`);
  } catch (error) {
    setStatus(`保存失败：${error.message}`);
  } finally {
    $("saveButton").disabled = false;
  }
}

function downloadCurrent() {
  if (!state.svg || !state.current) return;
  const blob = new Blob([serializeSvg()], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${state.current.name || state.productCode || "product-dimensions"}.svg`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

productSelect.addEventListener("change", () => loadAsset(productSelect.value).catch((error) => setStatus(error.message)));
$("reloadButton").addEventListener("click", () => loadAsset(productSelect.value).catch((error) => setStatus(error.message)));
$("selectTool").addEventListener("click", () => setTool("select"));
$("dimensionTool").addEventListener("click", () => setTool("dimension"));
$("zoomIn").addEventListener("click", () => { state.zoom = Math.min(4, state.zoom * 1.2); applyZoom(); });
$("zoomOut").addEventListener("click", () => { state.zoom = Math.max(.1, state.zoom / 1.2); applyZoom(); });
$("zoomFit").addEventListener("click", fitCanvas);
$("downloadButton").addEventListener("click", downloadCurrent);
$("saveButton").addEventListener("click", saveCurrent);
$("deleteSelected").addEventListener("click", () => {
  if (!state.selected) return;
  const target = state.selected.closest?.("[data-editor-dimension]") || state.selected;
  selectElement(null);
  target.remove();
  renderDimensions();
  setStatus("选中标注已删除；保存后生效。 ");
});

[$("selectedValue"), $("selectedX"), $("selectedY"), $("selectedAngle")].forEach((input) => {
  input.addEventListener("input", () => {
    const text = selectedText();
    if (!text) return;
    text.textContent = $("selectedValue").value;
    setTextGeometry(text, Number($("selectedX").value || 0), Number($("selectedY").value || 0), Number($("selectedAngle").value || 0));
    renderDimensions();
  });
});

window.addEventListener("resize", () => { if (state.svg) fitCanvas(); });
loadCatalog().catch((error) => setStatus(`载入失败：${error.message}`));
