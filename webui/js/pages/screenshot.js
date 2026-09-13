/* 截图页：区域/全屏/窗口/延迟捕获 + After Capture 任务链 + 画布标注编辑
（画笔/高亮/形状/步进编号/文字/马赛克/裁剪/橡皮，撤销重做）+ 保存/复制 + 历史管理。
后端：shot_*（app/bridge/screenshot_api.py）。编辑为纯前端 canvas 实现。 */
(function () {
  "use strict";

  const refs = {};
  const MAX_DIM = 4096;        // 编辑分辨率上限（4K 原图无损；仅防超大图卡顿）
  const UNDO_DEPTH = 12;       // 撤销栈深度上限（按画布像素自适应，见 snapDepth）

  function snapDepth() {
    // 自适应深度：单帧 ImageData ≈ w*h*4 字节，总预算 96MB，[3, 12]；
    // 4K 画布单帧约 67MB → 深度自动降到 3，避免撤销栈吃掉数百 MB 内存
    const bytes = Math.max(1, state.w * state.h * 4);
    return Math.max(3, Math.min(UNDO_DEPTH,
      Math.floor((96 * 1024 * 1024) / bytes)));
  }
  const state = {
    img: null,                   // Image 原图
    tool: "brush",               // brush | highlight | rect | ellipse | arrow |
                                 // text | step | mosaic | crop | eraser
    color: "#ff4d4f",
    size: 4,
    stepN: 0,                    // 步进编号计数（切换到编号工具时归零）
    cropRect: null,              // 裁剪拖选矩形
    drawing: false,
    moved: false,
    start: null, cur: null, pts: [],
    snap: null,                  // stroke 前快照（形状/马赛克/裁剪预览用）
    undo: [], redo: [],
    file: null,                  // 当前来源历史项（{path,name}）
  };

  /* ---------------- 画布与坐标 ---------------- */
  function setupCanvas() {
    const c = refs.canvas;
    const ctx = c.getContext("2d");
    const img = state.img;
    let w = img.naturalWidth, h = img.naturalHeight;
    if (w > MAX_DIM || h > MAX_DIM) {
      const s = Math.min(MAX_DIM / w, MAX_DIM / h, 1);
      w = Math.round(w * s); h = Math.round(h * s);
    }
    c.width = w; c.height = h;
    ctx.drawImage(img, 0, 0, w, h);
    c.style.display = "block";   // 载入图片后显示画布
    state.w = w; state.h = h;
    state.undo = []; state.redo = [];
    if (refs.canvasEmpty) refs.canvasEmpty.style.display = "none";  // 有图隐藏空态
    fitCanvas();
  }

  function fitCanvas() {
    const c = refs.canvas;
    if (!state.img) return;
    const box = refs.canvasBox;
    const maxW = Math.max(320, box.clientWidth - 24);
    /* v5.5：编辑窗口高度约束——
       常规图：宽高双约束完整放入一屏（含下方操作区，预留约 150px）；
       极端长宽比（长截图）：完整缩放会窄到无法标注，改为保底 320px 可用宽度，
       画布容器内部滚动（box maxHeight），页面本身不溢出、操作区固定可见。 */
    const top = box.getBoundingClientRect().top;
    const maxH = Math.max(160, window.innerHeight - top - 150);
    let cssW = Math.min(maxW, state.w);
    let cssH = cssW * state.h / state.w;
    if (cssH > maxH) {
      const fitW = maxH * state.w / state.h;   // 完整放入一屏所需宽度
      if (fitW >= Math.min(maxW, 320)) {
        cssH = maxH;
        cssW = fitW;
        box.style.maxHeight = "";
      } else {
        cssW = Math.min(maxW, 320);
        cssH = cssW * state.h / state.w;
        box.style.maxHeight = (maxH + 22) + "px";   // 补 padding/边框
      }
    } else {
      box.style.maxHeight = "";
    }
    c.style.width = Math.round(cssW) + "px";
    c.style.height = Math.round(cssH) + "px";
  }

  function evPos(e) {
    const rect = state.canvas.getBoundingClientRect();
    return [
      (e.clientX - rect.left) * (state.w / rect.width),
      (e.clientY - rect.top) * (state.h / rect.height),
    ];
  }

  /* ---------------- 撤销 / 重做 ---------------- */
  function pushUndo() {
    const ctx = state.ctx;
    state.undo.push(ctx.getImageData(0, 0, state.w, state.h));
    if (state.undo.length > snapDepth()) state.undo.shift();
    state.redo.length = 0;
    updateBtns();
  }
  function undo() {
    const ctx = state.ctx;
    if (!state.undo.length) return;
    state.redo.push(ctx.getImageData(0, 0, state.w, state.h));
    if (state.redo.length > snapDepth()) state.redo.shift();
    ctx.putImageData(state.undo.pop(), 0, 0);
    updateBtns();
  }
  function redo() {
    const ctx = state.ctx;
    if (!state.redo.length) return;
    state.undo.push(ctx.getImageData(0, 0, state.w, state.h));
    if (state.undo.length > snapDepth()) state.undo.shift();
    ctx.putImageData(state.redo.pop(), 0, 0);
    updateBtns();
  }
  function resetCanvas() {
    if (!state.img) return;
    const ctx = state.ctx;
    state.canvas.width = state.w;
    ctx.drawImage(state.img, 0, 0, state.w, state.h);
    state.undo = []; state.redo = [];
    updateBtns();
    App.toast("已还原至原始截图", "ok");
  }
  function updateBtns() {
    if (refs.btnUndo) refs.btnUndo.disabled = !state.undo.length;
    if (refs.btnRedo) refs.btnRedo.disabled = !state.redo.length;
  }

  /* ---------------- 绘制原语 ---------------- */
  function applyStyle(ctx) {
    ctx.strokeStyle = state.color;
    ctx.fillStyle = state.color;
    ctx.lineWidth = state.tool === "eraser" ? state.size * 3 : state.size;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
  }

  function drawShape(ctx, x1, y1, x2, y2) {
    applyStyle(ctx);
    ctx.beginPath();
    if (state.tool === "rect") {
      ctx.rect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
      ctx.stroke();
    } else if (state.tool === "ellipse") {
      ctx.ellipse((x1 + x2) / 2, (y1 + y2) / 2,
        Math.abs(x2 - x1) / 2, Math.abs(y2 - y1) / 2, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else if (state.tool === "arrow") {
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.stroke();
      const head = Math.min(20, state.size * 5 + 6);
      const ang = Math.atan2(y2 - y1, x2 - x1);
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - head * Math.cos(ang - Math.PI / 6), y2 - head * Math.sin(ang - Math.PI / 6));
      ctx.lineTo(x2 - head * Math.cos(ang + Math.PI / 6), y2 - head * Math.sin(ang + Math.PI / 6));
      ctx.closePath(); ctx.fill();
    }
  }

  function applyMosaic(points) {
    if (!points.length) return;
    let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    for (const [px, py] of points) {
      x1 = Math.min(x1, px); y1 = Math.min(y1, py);
      x2 = Math.max(x2, px); y2 = Math.max(y2, py);
    }
    const sx = x1, sy = y1;
    const sw = Math.min(state.w - sx, Math.max(8, x2 - x1 + 1));
    const sh = Math.min(state.h - sy, Math.max(8, y2 - y1 + 1));
    const block = Math.max(8, state.size * 2);
    const cols = Math.max(1, Math.ceil(sw / block));
    const rows = Math.max(1, Math.ceil(sh / block));
    const tmp = document.createElement("canvas");
    tmp.width = cols; tmp.height = rows;
    const tctx = tmp.getContext("2d");
    tctx.drawImage(state.canvas, sx, sy, sw, sh, 0, 0, cols, rows);
    const ctx = state.ctx;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(tmp, 0, 0, cols, rows, sx, sy, sw, sh);
    ctx.imageSmoothingEnabled = true;
  }

  /* 步进编号：递增圆圈（ShareX Step 工具），切换到该工具时计数归零 */
  function drawStep(x, y) {
    state.stepN += 1;
    const ctx = state.ctx;
    const r = Math.max(12, state.size * 2 + 8);
    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = state.color;
    ctx.fill();
    ctx.lineWidth = Math.max(2, state.size);
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    ctx.font = `bold ${Math.round(r * 1.1)}px Consolas, monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(state.stepN), x, y + 1);
    ctx.restore();
  }

  /* Chaikin 曲线平滑（用于铅笔工具） */
  function chaikinSmooth(pts, iterations) {
    if (pts.length < 3) return pts;
    let result = pts;
    for (let iter = 0; iter < (iterations || 2); iter++) {
      const next = [result[0]];
      for (let i = 0; i < result.length - 1; i++) {
        const [ax, ay] = result[i], [bx, by] = result[i + 1];
        next.push([ax * 0.75 + bx * 0.25, ay * 0.75 + by * 0.25]);
        next.push([ax * 0.25 + bx * 0.75, ay * 0.25 + by * 0.75]);
      }
      next.push(result[result.length - 1]);
      result = next;
    }
    return result;
  }

  function drawFreehand(ctx, pts) {
    if (pts.length < 2) {
      if (pts.length === 1) {
        ctx.beginPath();
        ctx.arc(pts[0][0], pts[0][1], state.size / 2, 0, Math.PI * 2);
        ctx.fill();
      }
      return;
    }
    const smooth = chaikinSmooth(pts);
    ctx.beginPath();
    ctx.moveTo(smooth[0][0], smooth[0][1]);
    for (let i = 1; i < smooth.length; i++) ctx.lineTo(smooth[i][0], smooth[i][1]);
    ctx.stroke();
  }

  function drawCurvedArrow(ctx, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len < 3) return;
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    const nx = -dy / len, ny = dx / len;
    const off = len * 0.25;
    const cx = mx + nx * off, cy = my + ny * off;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.quadraticCurveTo(cx, cy, x2, y2);
    ctx.stroke();
    const tax = x2 - (cx - x2) * 0.1, tay = y2 - (cy - y2) * 0.1;
    const ang = Math.atan2(y2 - tay, x2 - tax);
    const head = Math.min(18, state.size * 4 + 6);
    ctx.beginPath();
    ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - head * Math.cos(ang - Math.PI / 6), y2 - head * Math.sin(ang - Math.PI / 6));
    ctx.lineTo(x2 - head * Math.cos(ang + Math.PI / 6), y2 - head * Math.sin(ang + Math.PI / 6));
    ctx.closePath(); ctx.fill();
  }

  function drawShadowRect(ctx, x1, y1, x2, y2) {
    const rx = Math.min(x1, x2), ry = Math.min(y1, y2);
    const rw = Math.abs(x2 - x1), rh = Math.abs(y2 - y1);
    ctx.save();
    ctx.shadowColor = "rgba(0,0,0,0.45)";
    ctx.shadowBlur = 12;
    ctx.shadowOffsetX = 4;
    ctx.shadowOffsetY = 4;
    ctx.fillStyle = state.color + "33";
    ctx.fillRect(rx, ry, rw, rh);
    ctx.restore();
    applyStyle(ctx);
    ctx.strokeRect(rx, ry, rw, rh);
  }

  async function drawCallout(x, y, prefill) {
    const text = prefill || await App.prompt("标注文字", "", "输入标注框内容，框体自动适应文字大小。");
    if (!text || !text.trim()) return;
    const ctx = state.ctx;
    const fontSize = Math.max(14, state.size * 3 + 8);
    ctx.save();
    ctx.font = `bold ${fontSize}px "Microsoft YaHei", sans-serif`;
    const metrics = ctx.measureText(text);
    const pad = 10;
    const bw = metrics.width + pad * 2;
    const bh = fontSize + pad * 2;
    const bx = x, by = y - bh - 14;
    const r = 8;
    ctx.beginPath();
    ctx.moveTo(bx + r, by);
    ctx.arcTo(bx + bw, by, bx + bw, by + bh, r);
    ctx.arcTo(bx + bw, by + bh, bx, by + bh, r);
    ctx.lineTo(bx + bw / 2 + 8, by + bh);
    ctx.lineTo(bx + bw / 2, by + bh + 12);
    ctx.lineTo(bx + bw / 2 - 8, by + bh);
    ctx.arcTo(bx, by + bh, bx, by, r);
    ctx.arcTo(bx, by, bx + bw, by, r);
    ctx.closePath();
    ctx.fillStyle = state.color;
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.2)";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = "#ffffff";
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    ctx.fillText(text, bx + pad, by + bh / 2);
    ctx.restore();
  }

  async function drawStamp(x, y) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    const file = await new Promise((res) => {
      input.onchange = () => res(input.files[0] || null);
      input.click();
    });
    if (!file) return;
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = url; });
      const maxDim = Math.max(32, state.size * 16);
      let sw = img.naturalWidth, sh = img.naturalHeight;
      const scale = Math.min(maxDim / sw, maxDim / sh, 1);
      sw = Math.round(sw * scale); sh = Math.round(sh * scale);
      const ctx = state.ctx;
      ctx.drawImage(img, x - sw / 2, y - sh / 2, sw, sh);
    } finally { URL.revokeObjectURL(url); }
  }

  /* 裁剪：按选区重置画布（裁剪后清空撤销栈——历史帧与新画布尺寸不一致） */
  function applyCrop(x1, y1, x2, y2) {
    const w = Math.round(Math.abs(x2 - x1));
    const h = Math.round(Math.abs(y2 - y1));
    if (w < 2 || h < 2) return;
    const sx = Math.min(x1, x2), sy = Math.min(y1, y2);
    const tmp = document.createElement("canvas");
    tmp.width = w; tmp.height = h;
    tmp.getContext("2d").drawImage(state.canvas, sx, sy, w, h, 0, 0, w, h);
    const c = state.canvas;
    c.width = w; c.height = h;   // 重置画布（内容被清空）
    state.ctx.drawImage(tmp, 0, 0);
    state.w = w; state.h = h;
    state.undo = []; state.redo = [];
    updateBtns();
    fitCanvas();
    App.toast(`已裁剪为 ${w} × ${h}px（还原原图可恢复）`, "ok", 4000);
  }

  /* ---- 图像效果（ShareX ImageEffects 精选：圆角 / 灰度 / 反色） ---- */
  function applyEffect(kind) {
    if (!state.img) { App.toast("请先截图", "error"); return; }
    pushUndo();
    const ctx = state.ctx;
    const { w, h } = state;
    if (kind === "round") {
      // 圆角：以当前画布内容经圆角路径裁剪重绘（半径 = 短边 6%），已有标注保留
      const tmp = document.createElement("canvas");
      tmp.width = w; tmp.height = h;
      tmp.getContext("2d").drawImage(state.canvas, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const r = Math.round(Math.min(w, h) * 0.06);
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(r, 0);
      ctx.arcTo(w, 0, w, h, r);
      ctx.arcTo(w, h, 0, h, r);
      ctx.arcTo(0, h, 0, 0, r);
      ctx.arcTo(0, 0, w, 0, r);
      ctx.closePath();
      ctx.clip();
      ctx.drawImage(tmp, 0, 0);
      ctx.restore();
    } else if (kind === "gray") {
      const d = ctx.getImageData(0, 0, w, h);
      const p = d.data;
      for (let i = 0; i < p.length; i += 4) {
        const g = (p[i] * 299 + p[i + 1] * 587 + p[i + 2] * 114) / 1000;
        p[i] = p[i + 1] = p[i + 2] = g;
      }
      ctx.putImageData(d, 0, 0);
    } else if (kind === "invert") {
      const d = ctx.getImageData(0, 0, w, h);
      const p = d.data;
      for (let i = 0; i < p.length; i += 4) {
        p[i] = 255 - p[i]; p[i + 1] = 255 - p[i + 1]; p[i + 2] = 255 - p[i + 2];
      }
      ctx.putImageData(d, 0, 0);
    }
    App.toast({ round: "已应用圆角", gray: "已应用灰度", invert: "已应用反色" }[kind] + "（可撤销）", "ok", 3000);
  }

  /* 尺寸缩放（ShareX 编辑器 Resize）：按目标宽等比重采样；缩放后清空撤销栈 */
  async function applyResize() {
    if (!state.img) { App.toast("请先截图", "error"); return; }
    const val = await App.prompt("缩放为宽度（px）", String(state.w),
      `当前 ${state.w} × ${state.h}px，高度按比例缩放；范围 16 – 4096。`);
    if (val == null) return;
    const tw = Math.max(16, Math.min(4096, parseInt(val, 10) || 0));
    if (tw === state.w) return;
    const th = Math.max(1, Math.round(state.h * tw / state.w));
    const tmp = document.createElement("canvas");
    tmp.width = tw; tmp.height = th;
    const tctx = tmp.getContext("2d");
    tctx.imageSmoothingEnabled = true;
    tctx.imageSmoothingQuality = "high";
    tctx.drawImage(state.canvas, 0, 0, tw, th);
    const c = state.canvas;
    c.width = tw; c.height = th;
    state.ctx.drawImage(tmp, 0, 0);
    state.w = tw; state.h = th;
    state.undo = []; state.redo = [];
    updateBtns();
    fitCanvas();
    App.toast(`已缩放为 ${tw} × ${th}px（还原原图可恢复）`, "ok", 4000);
  }

  /* ---------------- 交互 ---------------- */
  function onDown(e) {
    if (!state.img || state.tool === "text" || state.tool === "step" ||
        state.tool === "callout" || state.tool === "stamp") return;
    e.preventDefault();
    state.drawing = true; state.moved = false;
    state.pts = [];
    const [x, y] = evPos(e);
    state.start = { x, y }; state.cur = { x, y };
    const ctx = state.ctx;
    if (state.tool === "brush" || state.tool === "eraser" || state.tool === "highlight" || state.tool === "freehand") {
      pushUndo();
      if (state.tool === "highlight") {
        // 荧光笔：正片叠底半透明（叠加深色也不遮内容）
        ctx.save();
        ctx.globalCompositeOperation = "multiply";
        ctx.strokeStyle = state.color;
        ctx.globalAlpha = 0.45;
        ctx.lineWidth = state.size * 6;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        ctx.moveTo(x, y); ctx.lineTo(x + 0.01, y + 0.01); ctx.stroke();
        return;
      }
      if (state.tool === "freehand") {
        state.pts.push([x, y]);
        ctx.beginPath();
        applyStyle(ctx);
        ctx.moveTo(x, y); ctx.lineTo(x + 0.01, y + 0.01); ctx.stroke();
        return;
      }
      ctx.beginPath();
      ctx.globalCompositeOperation = state.tool === "eraser" ? "destination-out" : "source-over";
      applyStyle(ctx);
      ctx.moveTo(x, y); ctx.lineTo(x + 0.01, y + 0.01); ctx.stroke();
    } else {
      state.snap = ctx.getImageData(0, 0, state.w, state.h);
    }
  }

  function onMove(e) {
    if (!state.drawing) return;
    const [x, y] = evPos(e);
    state.moved = true;
    state.cur = { x, y };
    const ctx = state.ctx;
    if (state.tool === "brush" || state.tool === "eraser") {
      ctx.lineTo(x, y); ctx.stroke();
    } else if (state.tool === "freehand") {
      state.pts.push([x, y]);
      ctx.lineTo(x, y); ctx.stroke();
    } else if (state.tool === "highlight") {
      ctx.lineTo(x, y); ctx.stroke();
    } else if (state.tool === "rect" || state.tool === "shadow_rect" ||
               state.tool === "ellipse" || state.tool === "arrow" ||
               state.tool === "curved_arrow" || state.tool === "crop") {
      ctx.putImageData(state.snap, 0, 0);
      if (state.tool === "crop") {
        // 裁剪预览：白色虚线矩形
        ctx.save();
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = "rgba(255,255,255,.9)";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(Math.min(x, state.start.x), Math.min(y, state.start.y),
          Math.abs(x - state.start.x), Math.abs(y - state.start.y));
        ctx.restore();
      } else if (state.tool === "shadow_rect") {
        drawShadowRect(ctx, state.start.x, state.start.y, x, y);
      } else if (state.tool === "curved_arrow") {
        drawCurvedArrow(ctx, state.start.x, state.start.y, x, y);
      } else {
        drawShape(ctx, state.start.x, state.start.y, x, y);
      }
    } else if (state.tool === "mosaic") {
      ctx.putImageData(state.snap, 0, 0);
      ctx.fillStyle = "rgba(120,120,120,0.45)";
      ctx.fillRect(x - state.size, y - state.size, state.size * 2, state.size * 2);
      state.pts.push([x, y]);
    }
  }

  function onUp() {
    if (!state.drawing) return;
    state.drawing = false;
    const ctx = state.ctx;
    if (state.tool === "brush" || state.tool === "eraser") {
      ctx.globalCompositeOperation = "source-over";
    } else if (state.tool === "freehand") {
      ctx.putImageData(state.snap, 0, 0);
      drawFreehand(ctx, state.pts.length ? state.pts : [[state.start.x, state.start.y]]);
      pushUndo();
    } else if (state.tool === "highlight") {
      ctx.restore();
    } else if (state.tool === "mosaic") {
      ctx.putImageData(state.snap, 0, 0);
      applyMosaic(state.pts.length ? state.pts : [[state.start.x, state.start.y]]);
      pushUndo();
    } else if (state.tool === "crop") {
      ctx.putImageData(state.snap, 0, 0);
      state.snap = null;
      if (state.moved) {
        applyCrop(state.start.x, state.start.y, state.cur.x, state.cur.y);
      }
      return;
    } else if (state.tool === "rect" || state.tool === "shadow_rect" ||
               state.tool === "ellipse" || state.tool === "arrow" ||
               state.tool === "curved_arrow") {
      if (!state.moved) { ctx.putImageData(state.snap, 0, 0); }
      else pushUndo();
    }
    state.snap = null;
  }

  async function onTextClick(e) {
    if (!state.img || state.drawing) return;
    const [x, y] = evPos(e);
    if (state.tool === "step") {
      pushUndo();
      drawStep(x, y);
      return;
    }
    if (state.tool === "callout") {
      const text = await App.prompt("标注框文字", "", App.h("p", { class: "hint" }, "在点击位置插入标注气泡框。"));
      if (text == null || !String(text).trim()) return;
      pushUndo();
      await drawCallout(x, y, text);
      return;
    }
    if (state.tool === "stamp") {
      pushUndo();
      await drawStamp(x, y);
      return;
    }
    if (state.tool !== "text") return;
    const text = await App.prompt("标注文字", "", App.h("p", { class: "hint" }, "在点击位置插入文字，字号 = 画笔粗细 × 4。"));
    if (text == null || !String(text).trim()) return;
    pushUndo();
    const ctx = state.ctx;
    applyStyle(ctx);
    ctx.font = `bold ${state.size * 4}px "Microsoft YaHei", sans-serif`;
    ctx.textBaseline = "top";
    ctx.strokeStyle = "rgba(0,0,0,0.45)";
    ctx.lineWidth = 4;
    ctx.strokeText(text, x, y);
    ctx.fillStyle = state.color;
    ctx.fillText(text, x, y);
  }

  /* ---------------- 加载 / 保存 / 复制 ---------------- */
  async function loadFromCapture(r) {
    if (!r || !r.ok) { if (r && r.err) App.toast(r.err, "error", 4000); return; }
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = r.data.img; });
    state.img = img;
    state.file = null;
    setupCanvas();
    const modeName = r.data.mode === "window" ? "活动窗口"
      : r.data.mode === "region" ? "区域"
      : r.data.mode === "clipboard" ? "剪贴板" : "全屏";
    const afterBits = [];
    if (r.data.saved) afterBits.push("已存 " + r.data.saved);
    if (r.data.copied) afterBits.push("已复制");
    if (r.data.path_copied) afterBits.push("路径已复制");
    if (r.data.revealed) afterBits.push("已定位");
    refs.info.textContent = `${r.data.w} × ${r.data.h}px · ${modeName}` +
      (afterBits.length ? " · " + afterBits.join(" · ") : "");
    App.toast("截图完成" + (afterBits.length ? "（" + afterBits.join("，") + "）" : "，可标注后保存"), "ok", 4000);
  }

  async function save(label) {
    if (!state.img) { App.toast("请先截图", "error"); return; }
    const url = state.canvas.toDataURL("image/png");
    const r = await App.tryCall("shot_save", url, label || "截图");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast(`已保存：${r.data.path}`, "ok", 5000);
    loadHistory();
  }

  async function copy() {
    if (!state.img) { App.toast("请先截图", "error"); return; }
    const url = state.canvas.toDataURL("image/png");
    const r = await App.tryCall("shot_copy", url);
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast("已复制到剪贴板", "ok");
  }

  async function loadHistory() {
    const r = await App.tryCall("shot_history");
    if (!r.ok) { refs.history.innerHTML = ""; return; }
    const list = refs.history;
    list.innerHTML = "";
    const items = r.data || [];
    if (!items.length) {
      list.appendChild(App.h("div", { class: "empty" }, "暂无截图记录"));
      return;
    }
    for (const it of items) {
      const thumb = App.h("img", {
        src: it.thumb || "",
        alt: "",
        style: {
          width: "42px", height: "42px", objectFit: "cover",
          borderRadius: "6px", border: "1px solid var(--border)",
          background: "var(--bg2)", flex: "none", cursor: "pointer",
        },
        onclick: () => openHistory(it),
        title: "点击载入编辑器",
      });
      list.appendChild(App.h("div", { class: "list-item", style: { gap: "10px" } },
        thumb,
        App.h("span", { class: "li-main" },
          App.h("div", { class: "li-title mono", style: { wordBreak: "break-all" } }, it.name),
          App.h("div", { class: "li-sub" }, App.fmtDate(it.time) + " · " + App.fmtBytes(it.size)),
        ),
        App.h("button", { class: "btn sm", onclick: () => openHistory(it) }, "编辑"),
        App.h("button", { class: "btn sm", title: "复制原图到剪贴板", onclick: async () => {
          const r = await App.tryCall("shot_open", it.path);
          if (!r.ok) { App.toast(r.err, "error", 5000); return; }
          const c = await App.tryCall("shot_copy", r.data.img);
          if (c.ok) App.toast("已复制到剪贴板", "ok");
          else App.toast(c.err, "error", 5000);
        } }, "复制"),
        App.h("button", { class: "btn sm", onclick: async () => {
          await App.tryCall("shot_reveal", it.path);
        }, title: "在资源管理器中定位" }, "定位"),
        App.h("button", { class: "btn sm danger", onclick: async () => {
          if (!(await App.confirm("删除截图", `确定删除「${it.name}」？`))) return;
          const d = await App.tryCall("shot_delete", it.path);
          if (d.ok) { App.toast("已删除", "ok"); loadHistory(); }
          else App.toast(d.err, "error", 5000);
        } }, "删除"),
      ));
    }
  }

  async function openHistory(it) {
    const r = await App.tryCall("shot_open", it.path);
    if (!r.ok) { App.toast(r.err, "error", 5000); return; }
    const img = new Image();
    await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = r.data.img; });
    state.img = img;
    state.file = it;
    setupCanvas();
    refs.info.textContent = `${r.data.w} × ${r.data.h}px · 历史：${it.name}`;
    App.toast("已加载历史截图，可继续标注", "ok");
  }

  /* ---------------- 区域截图（ShareX 式全屏遮罩弹窗） ---------------- */
  let regionBusy = false;   // 遮罩进行中禁止并发再开（页面按钮 / 全局热键共用）

  async function regionCapture() {
    if (regionBusy) return;
    regionBusy = true;
    try {
      const r = await App.tryCall("shot_region_popup", "shot");
      if (!r.ok) App.toast(r.err, "error", 6000);
      /* 确认后经 shot_pick 事件把选区图载入编辑器；取消经 shot_pick_cancel 提示 */
    } finally {
      regionBusy = false;
    }
  }

  /* 遮罩结果事件接线：
     - shot_pick：弹窗选区截图 → 载入本页编辑器（app.js 先导航到本页再调 App.shotLoadPick）
     - shot_region_rect / shot_region_cancel：录制区域坐标（Promise 等待器） */
  let rectWaiters = [];
  if (window.App) {
    App.shotRegionCapture = regionCapture;
    App.shotLoadPick = (d) => {
      if (!d || !d.img) return;
      // 透传任务链结果（saved/copied/path_copied/revealed），loadFromCapture 据此显示
      loadFromCapture({
        ok: true,
        data: {
          img: d.img, w: d.w, h: d.h, mode: "region",
          saved: d.saved, copied: d.copied,
          path_copied: d.path_copied, revealed: d.revealed,
        },
      });
    };
    App.pickRegionRect = () => new Promise((res) => rectWaiters.push(res));
  }
  App.on("shot_region_rect", (d) => {
    const ws = rectWaiters; rectWaiters = [];
    ws.forEach((f) => f(d && d.rect ? d.rect : null));
  });
  App.on("shot_region_cancel", () => {
    const ws = rectWaiters; rectWaiters = [];
    ws.forEach((f) => f(null));
  });

  /* ---------------- v5.3 滚动截图：补上此前无人接收的三个事件 ----------------
     后端 scroll_pick_window() 会先发 scroll_hint 再 sleep 3 秒，而 scroll_capture()
     在后台线程里发进度与结果 —— 前端此前对这三个事件零订阅，于是：
     ① 那 3 秒静默等待没有任何提示；② 拼接好的长图（scroll_done.data_url）被丢弃。
     由 scripts/check_contract.py 的"后端发出但无人订阅"检查查出。 */
  App.on("scroll_hint", (msg) => App.toast(String(msg || "准备滚动截图…"), "info", 4000));
  App.on("scroll_progress", (d) => {
    /* 后端只在"开始捕获"与"开始拼接"各发一次，用 toast 足够且不依赖页面状态行 */
    const t = d && d.status === "stitching"
      ? `正在拼接长图（${(d && d.frames) || 0} 帧）…`
      : `正在捕获（第 ${(d && d.frames) || 0} 帧）…`;
    App.toast(t, "info", 3000);
  });
  App.on("scroll_done", (d) => {
    if (!d || d.error) { App.toast((d && d.error) || "滚动截图失败", "error", 6000); return; }
    if (!d.data_url) return;
    App.navigate("screenshot").then(() => {
      if (App.shotLoadPick) App.shotLoadPick({ img: d.data_url, w: d.w, h: d.h, mode: "scroll" });
    });
    App.toast(`滚动截图完成（${d.frames || 0} 帧）`, "ok", 4000);
  });

  /* ---------------- 录屏（GIF 动图 / MP4 视频 + 系统声音） ---------------- */
  const recUi = {
    btn: null, info: null, fps: null, fmt: null,
    scopeSel: null, audioTgl: null, audioRow: null, running: false,
  };

  function paintRecUi() {
    if (!recUi.btn) return;
    recUi.btn.textContent = recUi.running ? "■ 停止录制" : "● 开始录制";
    recUi.btn.classList.toggle("primary", !recUi.running);
    recUi.btn.classList.toggle("danger", recUi.running);
    const isVideo = recUi.fmt && recUi.fmt.value === "video";
    if (recUi.fps) recUi.fps.disabled = recUi.running;
    // 范围选择 GIF / 视频均可用（后端两种格式都支持区域裁剪）
    if (recUi.scopeSel) recUi.scopeSel.disabled = recUi.running;
    if (recUi.audioTgl) recUi.audioTgl.disabled = recUi.running || !isVideo;
    if (recUi.audioRow) recUi.audioRow.style.opacity = isVideo ? "" : "0.45";
  }

  function fillFpsOptions() {
    const fmt = recUi.fmt.value;
    const list = fmt === "video" ? [10, 15, 30] : [2, 5, 10];
    recUi.fps.innerHTML = "";
    for (const v of list) {
      recUi.fps.appendChild(App.h("option", { value: v }, v + " fps"));
    }
    const cfg = App.state.cfg || {};
    const cur = parseInt(cfg.recorder_fps, 10);
    recUi.fps.value = String(list.includes(cur) ? cur : (fmt === "video" ? 15 : 5));
  }

  async function recStart() {
    if (recUi.running) return;
    const fmt = recUi.fmt.value;
    const fps = parseInt(recUi.fps.value, 10);
    const opts = { format: fmt, fps: fps, audio: !!recUi.audioTgl.checked };
    if (recUi.scopeSel.value === "region") {
      // GIF / 视频均支持区域裁剪：必须先弹遮罩，否则 shot_region_rect 永不返回
      App.toast("请在弹出的全屏遮罩中圈选要录制的区域…", "info", 5000);
      const pop = await App.tryCall("shot_region_popup", "record");
      if (!pop.ok) { App.toast(pop.err, "error", 6000); return; }
      const region = await App.pickRegionRect();
      if (!region) { App.toast("已取消区域录制", "info"); return; }
      opts.region = region;
    }
    if (fmt === "gif") {
      App.toast("GIF 录制会包含本窗口，建议先最小化", "info", 4000);
    }
    const r = await App.tryCall("rec_start", opts);
    if (!r.ok) { App.toast(r.err, "error", 8000); return; }
    recUi.running = true;
    paintRecUi();
    if (recUi.info) {
      recUi.info.textContent = fmt === "video"
        ? "视频录制中…（窗口已自动隐藏，结束自动恢复）"
        : "GIF 录制中…";
    }
  }
  async function recStop() {
    const r = await App.tryCall("rec_stop");
    if (!r.ok) { App.toast(r.err, "error", 6000); return; }
    App.toast("已停止，正在编码保存…", "info");
    if (recUi.info) recUi.info.textContent = "正在编码保存…";
  }
  async function recSync() {
    const r = await App.tryCall("rec_get_state");
    if (!r.ok) return;
    recUi.running = !!r.data.running;
    paintRecUi();
    if (recUi.info) {
      recUi.info.textContent = r.data.running
        ? `录制中 · ${r.data.elapsed}s · ${r.data.frames} 帧`
        : "未录制";
    }
  }
  App.on("rec_state", (s) => {
    if (!s) return;
    recUi.running = !!s.running;
    paintRecUi();
    if (recUi.info && s.running) {
      recUi.info.textContent = `录制中 · ${s.elapsed}s · ${s.frames} 帧`;
    }
  });
  App.on("rec_done", (r) => {
    recUi.running = false;
    paintRecUi();
    if (recUi.info) recUi.info.textContent = "未录制";
    if (!r || !r.ok) { App.toast((r && r.err) || "录制失败", "error", 8000); return; }
    const kind = r.type === "video" ? "MP4 视频" : r.type === "gif" ? "GIF 动图" : "录制";
    const audioTxt = r.audio ? "（含系统声音）" : "";
    const note = r.note || "";
    App.toast(`录制完成：${kind}${audioTxt}（${r.frames} 帧，${App.fmtBytes(r.size)}）${note}`,
      "ok", 7000);
    if (r.path) App.toast("已保存：\n" + r.path, "ok", 8000);
  });

  function mountRecCard() {
    const cfg = App.state.cfg || {};
    recUi.fmt = App.h("select", { class: "input", style: { width: "140px" } },
      App.h("option", { value: "video" }, "MP4 视频（可录音）"),
      App.h("option", { value: "gif" }, "GIF 动图（低配）"));
    recUi.fmt.value = (cfg.recorder_format || "video") === "gif" ? "gif" : "video";
    recUi.fps = App.h("select", { class: "input", style: { width: "92px" } });
    recUi.fps.title = "录制帧率（越高越流畅，文件越大）";
    fillFpsOptions();
    recUi.scopeSel = App.h("select", { class: "input", style: { width: "140px" } },
      App.h("option", { value: "full" }, "整个屏幕"),
      App.h("option", { value: "region" }, "自选区域（圈选）"));
    recUi.scopeSel.value = (cfg.recorder_scope || "full") === "region" ? "region" : "full";
    recUi.audioTgl = App.h("input", { type: "checkbox" });
    if (cfg.recorder_audio !== false) recUi.audioTgl.checked = true;

    async function persist() {
      await App.tryCall("rec_set_fps", parseInt(recUi.fps.value, 10), recUi.fmt.value);
      await App.tryCall("cfg_set", "recorder_format", recUi.fmt.value);
      await App.tryCall("cfg_set", "recorder_scope", recUi.scopeSel.value);
      await App.tryCall("cfg_set", "recorder_audio", recUi.audioTgl.checked);
    }
    recUi.fmt.addEventListener("change", () => { fillFpsOptions(); paintRecUi(); persist(); });
    recUi.fps.addEventListener("change", () => persist());
    recUi.scopeSel.addEventListener("change", () => persist());
    recUi.audioTgl.addEventListener("change", () => persist());

    recUi.btn = App.h("button", { class: "btn primary" }, "● 开始录制");
    recUi.btn.addEventListener("click", () => (recUi.running ? recStop() : recStart()));
    recUi.info = App.h("span", { class: "hint" }, "未录制");
    recUi.audioRow = App.row(
      App.h("label", { class: "switch" }, recUi.audioTgl, App.h("span", { class: "track" }),
        "同步录制系统声音"),
    );
    paintRecUi();
    /* 次级任务收纳：录制折叠分区默认收起（v3.5 多级结构） */
    return App.sec("屏幕录制（ShareX 式：MP4 视频 / GIF 动图）", [
      App.h("p", { class: "hint", style: { margin: "0 0 8px" } },
        "MP4 视频与 GIF 动图均支持整个屏幕或自选区域；录制期间自动隐藏本窗口，结束后恢复。" +
        "MP4 可同步录制系统声音。"),
      App.row(
        App.h("span", { class: "field-label" }, "格式："), recUi.fmt,
        App.h("span", { class: "field-label" }, "帧率："), recUi.fps,
        App.h("span", { class: "field-label" }, "范围："), recUi.scopeSel,
      ),
      App.row(recUi.audioRow, App.h("span", { style: { flex: 1 } }), recUi.btn, recUi.info),
    ], { open: false });
  }

  /* ---------------- 页面 ---------------- */
  const TOOLS = [
    ["brush", "画笔"], ["highlight", "高亮"], ["freehand", "铅笔"],
    ["rect", "矩形"], ["shadow_rect", "阴影矩形"], ["ellipse", "圆形"],
    ["arrow", "箭头"], ["curved_arrow", "曲线箭头"],
    ["callout", "标注框"], ["step", "编号"], ["text", "文字"],
    ["stamp", "图章"],
    ["mosaic", "马赛克"], ["crop", "裁剪"], ["eraser", "橡皮"],
  ];

  App.registerPage({
    id: "screenshot",
    title: "截图标注",
    icon: "camera",
    group: "工具与增效",

    mount(el) {
      // 延迟秒数（作用于全屏/活动窗口截图；区域截图即时）
      const delayIn = App.h("input", {
        class: "input", type: "number", min: "0", max: "60", value: "0",
        title: "延迟秒数：抓取下拉菜单等会随点击消失的界面（期间自动隐藏本窗口）",
        style: { width: "64px" },
      });
      async function captureWith(mode) {
        const delay = Math.max(0, Math.min(60, parseInt(delayIn.value, 10) || 0));
        const r = await App.tryCall("shot_capture", mode, delay);
        if (!r.ok) { App.toast(r.err, "error", 5000); return; }
        if (r.data && r.data.queued) {
          App.toast(`将在 ${r.data.delay}s 后截图（主窗口已隐藏）…`, "info",
            Math.ceil(r.data.delay * 1000) + 1500);
          return;  // 完成后经 shot_capture_done 事件载入编辑器
        }
        loadFromCapture(r);
      }

      // After Capture 任务链（ShareX 式截图后自动任务）
      const afterChecks = {};
      async function saveAfter() {
        await App.tryCall("shot_set_after",
          afterChecks.save.checked, afterChecks.copy.checked, afterChecks.edit.checked);
      }
      const initAfter = (App.state.cfg || {}).shot_after || {};
      const mkAfter = (key, label, title) => {
        afterChecks[key] = App.h("input", {
          type: "checkbox",
          ...(initAfter[key] === false ? {} : { checked: true }),
          onchange: saveAfter,
          title,
        });
        return App.h("label", { class: "chk" }, afterChecks[key], " " + label);
      };

      const afterModal = () => {
        /* v5.4 O3：任务链顺序可调（cfg shot_after.order） */
        const cfgAfter = (App.state.cfg || {}).shot_after || {};
        let order = Array.isArray(cfgAfter.order) && cfgAfter.order.length
          ? cfgAfter.order.slice()
          : ["save", "copy", "copy_path", "reveal"];
        const labels = { save: "保存文件", copy: "复制到剪贴板",
          copy_path: "复制文件路径", reveal: "定位到文件" };
        const orderBox = App.h("div", { style: { display: "flex", flexDirection: "column", gap: "4px", margin: "6px 0" } });
        function renderOrder() {
          orderBox.innerHTML = "";
          order.forEach((k, i) => {
            orderBox.appendChild(App.h("div", { class: "row", style: { margin: "0", flexWrap: "nowrap" } },
              App.h("span", { class: "hint mono", style: { width: "18px", flex: "none" } }, String(i + 1)),
              App.h("span", { style: { flex: "1" } }, labels[k] || k),
              App.h("button", { class: "btn xs", disabled: i === 0, title: "上移",
                onclick: () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; renderOrder(); } }, "▲"),
              App.h("button", { class: "btn xs", disabled: i === order.length - 1, title: "下移",
                onclick: () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; renderOrder(); } }, "▼"),
            ));
          });
          /* v5.4 O3：顺序即时持久化（勾选亦即时生效，行为一致） */
          App.tryCall("shot_set_after", null, null, null, null, null, order.slice());
        }
        renderOrder();
        return App.modal({
          title: "截图后自动任务（ShareX 式）",
          body: App.h("div",
            { style: { display: "flex", flexDirection: "column", gap: "8px" } },
            mkAfter("save", "保存文件", "区域/全屏/窗口截图完成后自动保存原图到截图目录"),
            mkAfter("copy", "复制到剪贴板", "截图完成后自动把原图写入系统剪贴板"),
            mkAfter("edit", "载入编辑器", "截图完成后自动打开截图页画布进行标注"),
            mkAfter("copy_path", "复制文件路径", "自动保存后把文件路径写入剪贴板"),
            mkAfter("reveal", "定位到文件", "自动保存后在资源管理器中定位该文件"),
            App.h("div", { class: "hint", style: { marginTop: "4px" } },
              "后端任务链执行顺序（仅作用于已勾选的任务）："),
            orderBox,
            App.h("div", { class: "hint" }, "勾选与顺序即时生效；「载入编辑器」由前端执行不受顺序影响。"),
          ),
          okText: "完成",
        });
      };

      /* v5.2 P4：捕获栏精简——常驻仅 3 捕获钮 + 延迟，低频进「更多捕获 ⋯」 */
      const captureBar = App.h("div", { class: "row", style: { flexWrap: "wrap" } },
        App.h("button", { class: "btn primary", onclick: regionCapture,
          title: "每个显示器弹出圈选遮罩：单击选窗口 / 拖动自选" }, "区域截图（圈选）"),
        App.h("button", { class: "btn", onclick: () => captureWith("full") }, "全屏截图"),
        App.h("button", { class: "btn", onclick: () => captureWith("window") }, "活动窗口"),
        App.row(
          App.h("span", { class: "field-label" }, "延迟"), delayIn,
          App.h("span", { class: "field-label" }, "秒"),
        ),
        App.h("button", { class: "btn", title: "更多捕获方式与目录设置",
          onclick: (e) => App.overflowMenu(e.currentTarget, [
            { label: "重拍上次区域", icon: "camera",
              hint: "ShareX 上次区域",
              onclick: async () => {
                const r = await App.tryCall("shot_capture_last_region");
                if (!r.ok) { App.toast(r.err, "info"); return; }
                await loadFromCapture(r);
              } },
            { label: "屏幕标尺", icon: "edit",
              hint: "测量长度与角度",
              onclick: async () => {
                const r = await App.tryCall("shot_region_popup", "ruler", "cursor");
                if (!r.ok) App.toast(r.err, "error", 5000);
              } },
            { label: "滚动截图", icon: "list",
              hint: "长截图拼接",
              onclick: async () => {
                const pw = await App.tryCall("scroll_pick_window");
                if (!pw.ok) { App.toast(pw.err, "error", 4000); return; }
                if (!pw.data) { App.toast("未选择窗口", "info"); return; }
                App.toast("正在滚动截图，请勿操作鼠标…", "info", 3000);
                /* 结果由 scroll_done 事件异步带回（见下方 App.on("scroll_done")） */
                const r = await App.tryCall("scroll_capture", pw.data.hwnd);
                if (!r.ok) { App.toast(r.err, "error", 5000); return; }
              } },
            "sep",
            { label: "截图后自动任务…", icon: "settings", onclick: afterModal },
            "sep",
            { label: "更改保存目录", icon: "folder",
              onclick: async () => {
                const r = await App.tryCall("shot_set_dir", null);
                if (r.ok) App.toast("截图目录：\n" + r.data, "ok", 5000);
              } },
            { label: "打开目录", icon: "folder",
              onclick: async () => {
                const r = await App.tryCall("shot_reveal", null);
                if (!r.ok) App.toast(r.err, "error", 4000);
              } },
          ]) }, "更多捕获 ⋯"),
      );

      /* v5.5：标注工具栏两行分组（修复此前 15 控件混排、flexWrap 随意换行、
         按钮 27px / 下拉 32px / 取色器原生高度 三种高度不齐的错乱观感）
         行 1 = 标注工具（6 常驻 + 工具▾）+ 笔刷（颜色/粗细）
         行 2 = 画布操作（撤销/重做/还原）+ 效果▾（圆角/灰度/反色/缩放收进菜单） */
      const TOOL_LABEL = Object.fromEntries(TOOLS);
      const TOOLS_MAIN = ["brush", "highlight", "rect", "arrow", "text", "mosaic"];
      const TOOLS_MORE = TOOLS.filter(([t]) => !TOOLS_MAIN.includes(t));
      const divider = () => App.h("span", { class: "divider",
        style: { width: "1px", height: "18px", background: "var(--border)" } });
      const tb = App.h("div", { class: "row tool-bar",
        style: { flexWrap: "wrap", rowGap: "6px" } });
      const tb2 = App.h("div", { class: "row tool-bar",
        style: { flexWrap: "wrap", rowGap: "6px", marginTop: "6px" } });
      refs.toolbar = tb;
      function setTool(t) {
        state.tool = t;
        [tb, tb2].forEach((bar) => bar.querySelectorAll(".tool-btn")
          .forEach((b) => b.classList.remove("active")));
        moreToolsBtn.textContent = "工具 ▾";
        const btn = tb.querySelector(`.tool-btn[data-tool="${t}"]`);
        if (btn) btn.classList.add("active");
        else if (TOOLS_MORE.some(([x]) => x === t)) {
          moreToolsBtn.classList.add("active");
          moreToolsBtn.textContent = TOOL_LABEL[t] + " ▾";
        }
      }
      TOOLS_MAIN.forEach((t) => {
        tb.appendChild(App.h("button", {
          class: "btn sm tool-btn", "data-tool": t,
          onclick: () => setTool(t),
        }, TOOL_LABEL[t]));   /* v5.5 修复：TOOLS_MAIN 为扁平 id 数组，此前误用
                                 [t, label] 解构字符串导致按钮只显示 1 个字母 */
      });
      const moreToolsBtn = App.h("button", {
        class: "btn sm tool-btn",
        title: "更多标注工具",
        onclick: (e) => App.overflowMenu(e.currentTarget,
          TOOLS_MORE.map(([t, label]) => ({ label, onclick: () => setTool(t) }))),
      }, "工具 ▾");
      tb.appendChild(moreToolsBtn);
      tb.appendChild(divider());
      /* v5.5：取色器 / 粗细下拉与 .btn.sm 统一 27px 高 */
      const color = App.h("input", {
        type: "color", value: state.color, title: "标注颜色",
        style: { width: "34px", height: "27px", padding: "2px",
                 border: "1px solid var(--border)", borderRadius: "6px",
                 background: "var(--card2)", cursor: "pointer", flex: "none" },
      });
      color.addEventListener("input", () => { state.color = color.value; });
      const sizeSel = App.h("select", { class: "input",
        style: { width: "76px", height: "27px", flex: "none" } },
        ["2", "4", "8", "16"].map((v) => App.h("option", { value: v }, v + "px")));
      sizeSel.value = "4";
      sizeSel.addEventListener("change", () => { state.size = parseInt(sizeSel.value, 10); });
      tb.appendChild(color);
      tb.appendChild(sizeSel);

      refs.btnUndo = App.h("button", { class: "btn sm", disabled: true, onclick: undo }, "↩ 撤销");
      refs.btnRedo = App.h("button", { class: "btn sm", disabled: true, onclick: redo }, "↪ 重做");
      tb2.appendChild(refs.btnUndo);
      tb2.appendChild(refs.btnRedo);
      tb2.appendChild(App.h("button", { class: "btn sm", onclick: resetCanvas }, "还原原图"));
      tb2.appendChild(divider());
      const effectsBtn = App.h("button", { class: "btn sm", title: "图像效果",
        onclick: (e) => App.overflowMenu(e.currentTarget, [
          { label: "圆角化边缘", icon: "image", hint: "可撤销",
            onclick: () => applyEffect("round") },
          { label: "黑白化", icon: "image", hint: "可撤销",
            onclick: () => applyEffect("gray") },
          { label: "颜色反转", icon: "image", hint: "可撤销",
            onclick: () => applyEffect("invert") },
          "sep",
          { label: "按宽度缩放…", icon: "filter", hint: "等比缩放（可还原原图）",
            onclick: applyResize },
        ]) }, "效果 ▾");
      tb2.appendChild(effectsBtn);
      setTool(state.tool);

      // 画布（初始隐藏，载入图片后显示，避免空黑块占位）
      const canvas = App.h("canvas", {
        style: { display: "none", maxWidth: "100%", borderRadius: "6px", border: "1px solid var(--border)", background: "#111", touchAction: "none" },
      });
      refs.canvas = canvas;
      state.canvas = canvas;
      state.ctx = canvas.getContext("2d");
      const box = App.h("div", { class: "shot-canvas-box" }, canvas);
      // 无图空态占位：消除「编辑画布」卡下方的大片空白
      refs.canvasEmpty = App.h("div", {
        class: "empty",
        style: { position: "absolute", inset: "12px", display: "flex",
          alignItems: "center", justifyContent: "center", pointerEvents: "none" },
      }, "编辑画布为空\n点击上方「区域截图 / 全屏 / 窗口」开始，或从历史 / 剪贴板载入");
      box.style.position = "relative";
      box.appendChild(refs.canvasEmpty);
      refs.canvasBox = box;
      const info = App.h("div", { class: "hint", style: { marginTop: "8px" } }, "先截图或将历史截图载入画布，再进行标注编辑。");
      refs.info = info;

      /* v5.2 P4：操作行精简——保存/复制常驻，其余进「更多 ⋯」 */
      /* v5.4 O2：上传目标扁平展开（默认图床 + 各自定义上传目标；菜单点击时动态构建） */
      const doUpload = async (provider, label) => {
        if (!state.img) { App.toast("请先截图", "error"); return; }
        const url = state.canvas.toDataURL("image/png");
        App.toast("正在上传" + (label ? "到 " + label : "") + "…", "info", 3000);
        const r = await App.tryCall("upload_image", url, provider || undefined);
        if (!r.ok) { App.toast(r.err, "error", 5000); return; }
        const link = r.data.url || r.data.link || "";
        if (link) {
          App.copyText(link).then((ok) =>
            App.toast(ok ? "上传成功，链接已复制" : "上传成功：" + link,
              "ok", ok ? 5000 : 8000));
        } else {
          App.toast("上传成功", "ok");
        }
      };
      const uploadMenuItems = () => {
        const targets = ((App.state.cfg || {}).upload_targets) || [];
        if (!targets.length) {
          return [{ label: "上传图床", icon: "globe",
            hint: "上传并复制链接（可在设置添加自定义目标）",
            onclick: () => doUpload((App.state.cfg || {}).upload_service || "imgur") }];
        }
        const items = [{ label: "上传图床（默认）", icon: "globe",
          hint: "使用设置中的默认图床",
          onclick: () => doUpload((App.state.cfg || {}).upload_service || "imgur") }];
        targets.forEach((t, i) => {
          items.push({ label: "上传到「" + t.name + "」", icon: "globe",
            onclick: () => doUpload("target:" + i, t.name) });
        });
        return items;
      };
      const ops = App.h("div", { class: "row", style: { marginTop: "10px", flexWrap: "wrap" } },
        App.h("button", { class: "btn primary", onclick: () => save("截图") }, "保存"),
        App.h("button", { class: "btn", onclick: copy }, "复制到剪贴板"),
        App.h("button", {
          class: "btn", title: "导入 / 分享 / 钉图",
          onclick: (e) => App.overflowMenu(e.currentTarget, [
            { label: "另存为…", icon: "filetext",
              onclick: async () => {
                const label = await App.prompt("另存为标签", "截图", "将按「日期_时间_标签.png」命名");
                if (label != null && String(label).trim()) save(String(label).trim());
              } },
            { label: "从剪贴板导入", icon: "clipboard",
              hint: "载入系统剪贴板图片",
              onclick: async () => {
                const r = await App.tryCall("shot_paste_clipboard");
                if (!r.ok) { App.toast(r.err, "info"); return; }
                await loadFromCapture(r);
              } },
            { label: "贴图到屏幕", icon: "camera",
              hint: "钉在屏幕最上层",
              onclick: async () => {
                if (!state.img) { App.toast("请先截图", "error"); return; }
                const url = state.canvas.toDataURL("image/png");
                const r = await App.tryCall("pin_image", url);
                if (!r.ok) { App.toast(r.err, "error", 5000); return; }
                App.toast("已贴图到屏幕（可拖拽/缩放）", "ok");
              } },
            ...uploadMenuItems(),
          ]) }, "更多 ⋯"),
      );

      canvas.addEventListener("mousedown", onDown);
      canvas.addEventListener("mousemove", onMove);
      canvas.addEventListener("mouseup", onUp);
      canvas.addEventListener("mouseleave", () => { if (state.drawing) onUp(); });
      canvas.addEventListener("click", onTextClick);
      /* v5.3：页面不可见时不重算画布尺寸（此前窗口 resize 会对隐藏页面做 DOM 计算） */
      window.addEventListener("resize", () => {
        if (!el.classList.contains("active")) return;
        fitCanvas();
      });

      // 历史
      refs.history = App.h("div", { class: "list", style: { maxHeight: "300px", overflow: "auto" } });

      el.appendChild(App.h("div", { class: "page-head" },
        App.h("h2", null, "截图标注"),
        App.h("div", { class: "sub" }, "区域圈选（单击选窗口 / 拖动自选 / 手柄调整）/ 全屏 / 窗口 / 延迟，" +
          "截图后自动保存与复制；标注含画笔、高亮、形状、步进编号、马赛克与裁剪；支持 ShareX 式 MP4 / GIF 录制"),
      ));
      el.appendChild(captureBar);
      el.appendChild(App.h("div", { class: "card", style: { marginTop: "12px" } },
        App.h("div", { class: "card-title" }, "编辑画布"),
        tb, tb2, box, info, ops,
      ));
      el.appendChild(mountRecCard());
      el.appendChild(App.sec("截图历史", [refs.history], { open: false }));

      loadHistory();
      recSync();
    },

    show() { if (state.img) fitCanvas(); },
  });
})();