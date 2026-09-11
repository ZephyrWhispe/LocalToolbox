/* 区域圈选遮罩（ShareX 式交互）：
 *   1. 移动鼠标 → 自动识别并高亮光标下顶层窗口（窗口吸附），单击即选中整个窗口；
 *   2. 按住拖动 → 自由圈选任意矩形；
 *   3. 松开手 → 进入调整模式：8 个手柄改尺寸、选区内拖动移位、选区外按下重新圈选；
 *   4. Enter / 双击 / 确认按钮 → 完成；ESC / 取消按钮 → 放弃。
 *
 * 运行环境两用：
 * - pywebview 遮罩弹窗（主用）：overlay.html 注入 window.__shotSnapQuery /
 *   __shotSnapBox（窗口吸附查询与显示器物理偏移），结果经 onDone/onCancel 回后端；
 * - 普通浏览器（预览/测试）：无吸附查询时自动跳过窗口识别，纯拖选可用。
 */
(function () {
  "use strict";

  const ZOOM = 8;          // 放大镜缩放倍数
  const MAG = 144;         // 放大镜边长（px）
  const SNAP_MS = 120;     // 窗口吸附查询节流（ms）
  const CLICK_MAX = 4;     // 判定“单击”的最大位移（原生 px）
  const MIN_SIZE = 3;      // 最小选区边长（原生 px）
  const HANDLE_R = 5;      // 手柄半径（px，视觉）

  const HANDLE_CURSORS = {
    nw: "nwse-resize", se: "nwse-resize",
    ne: "nesw-resize", sw: "nesw-resize",
    n: "ns-resize", s: "ns-resize", w: "ew-resize", e: "ew-resize",
  };

  function start({ imgSrc = "", onDone, onCancel, onRect, mode = "shot" } = {}) {
    const isRuler = mode === "ruler";   // 标尺模式：测线段长度/角度，不产图
    /* ---------------- DOM ---------------- */
    const overlay = document.createElement("div");
    overlay.style.cssText = [
      "position:fixed;inset:0;z-index:1200;background:rgba(8,10,14,.92);",
      "cursor:crosshair;overflow:hidden;user-select:none;",
    ].join("");
    document.body.appendChild(overlay);

    const img = new Image();
    img.style.cssText = "position:absolute;display:block;pointer-events:none;" +
      "user-select:none;-webkit-user-drag:none;";

    const mkDim = () => {
      const d = document.createElement("div");
      d.style.cssText = "position:absolute;background:rgba(0,0,0,.45);pointer-events:none;display:none;";
      return d;
    };
    const dims = [mkDim(), mkDim(), mkDim(), mkDim()]; // 上 下 左 右

    // 窗口吸附高亮
    const snapHi = document.createElement("div");
    snapHi.style.cssText = "position:absolute;display:none;pointer-events:none;" +
      "border:2px dashed #ffc857;background:rgba(255,200,87,.10);";
    const snapTag = document.createElement("div");
    snapTag.style.cssText = "position:absolute;display:none;pointer-events:none;padding:1px 6px;" +
      "border-radius:4px;background:#7a5b00;color:#ffe9b0;font:11px Consolas,monospace;";

    // 选区
    const sel = document.createElement("div");
    sel.style.cssText = "position:absolute;display:none;pointer-events:none;" +
      "border:1.5px solid #4f9cff;background:rgba(79,156,255,.10);";
    const label = document.createElement("div");
    label.style.cssText = "position:absolute;display:none;pointer-events:none;padding:2px 8px;" +
      "border-radius:4px;background:#1f2733;color:#aee3ff;font:12px Consolas,monospace;white-space:nowrap;";

    // 8 手柄（调整模式）
    const handles = {};
    for (const dir of Object.keys(HANDLE_CURSORS)) {
      const hd = document.createElement("div");
      hd.style.cssText = "position:absolute;display:none;z-index:5;width:" + (HANDLE_R * 2) + "px;height:" +
        (HANDLE_R * 2) + "px;margin:-" + HANDLE_R + "px 0 0 -" + HANDLE_R + "px;" +
        "border-radius:50%;background:#fff;border:2px solid #2f81f7;cursor:" +
        HANDLE_CURSORS[dir] + ";";
      overlay.appendChild(hd);
      handles[dir] = hd;
    }

    // 调整模式工具条
    const bar = document.createElement("div");
    bar.style.cssText = "position:absolute;display:none;z-index:6;display:none;gap:6px;";
    const mkBtn = (text, bg, fg) => {
      const b = document.createElement("button");
      b.textContent = text;
      b.style.cssText = "padding:5px 14px;border:none;border-radius:6px;cursor:pointer;" +
        "background:" + bg + ";color:" + fg + ";font:12.5px 'Microsoft YaHei UI',sans-serif;";
      return b;
    };
    const okBtn = mkBtn("确认 (Enter)", "#2f81f7", "#fff");
    const reBtn = mkBtn("重选", "#30363d", "#c9d1d9");
    const cancelBtn = mkBtn("取消 (ESC)", "#30363d", "#c9d1d9");
    bar.appendChild(okBtn); bar.appendChild(reBtn); bar.appendChild(cancelBtn);

    // 放大镜
    const mag = document.createElement("canvas");
    mag.width = MAG; mag.height = MAG;
    mag.style.cssText = "position:absolute;display:none;width:" + MAG + "px;height:" + MAG + "px;" +
      "border:1px solid #4f9cff;border-radius:8px;background:#000;box-shadow:0 4px 20px rgba(0,0,0,.6);";
    const mctx = mag.getContext("2d");

    // 标尺模式：全屏线段画布（pointer-events:none）
    const rc = document.createElement("canvas");
    rc.style.cssText = "position:absolute;inset:0;display:none;pointer-events:none;";
    const rctx = rc.getContext("2d");
    overlay.appendChild(rc);
    let rulerOrigin = null;      // 线段拖动起点（原生 px）
    let rulerLine = null;        // {x1,y1,x2,y2}

    // 顶部提示
    const hint = document.createElement("div");
    hint.style.cssText = "position:absolute;top:12px;left:50%;transform:translateX(-50%);" +
      "z-index:7;pointer-events:none;padding:5px 14px;border-radius:8px;" +
      "background:rgba(16,22,32,.88);border:1px solid rgba(140,190,255,.35);" +
      "color:#cfe3ff;font:12.5px 'Segoe UI','Microsoft YaHei UI',sans-serif;white-space:nowrap;";
    hint.textContent = "单击选中窗口 · 拖动自选区域 · ESC 取消";

    // 状态/进度提示（提交后、结果返回前；独立于 overlay，避免随遮罩一起隐藏）
    const status = document.createElement("div");
    status.style.cssText = "position:fixed;inset:0;z-index:1300;display:none;" +
      "align-items:center;justify-content:center;background:rgba(8,10,14,.55);" +
      "pointer-events:none;";
    const statusInner = document.createElement("div");
    statusInner.style.cssText = "padding:12px 22px;border-radius:10px;" +
      "background:#1f2733;border:1px solid rgba(140,190,255,.4);" +
      "color:#cfe3ff;font:13px 'Segoe UI','Microsoft YaHei UI',sans-serif;white-space:pre-wrap;";
    status.appendChild(statusInner);
    document.body.appendChild(status);
    function showStatus(text) {
      statusInner.textContent = text;
      status.style.display = "flex";
    }
    function hideStatus() {
      status.style.display = "none";
    }

    overlay.appendChild(img);
    dims.forEach((d) => overlay.appendChild(d));
    overlay.appendChild(snapHi);
    overlay.appendChild(snapTag);
    overlay.appendChild(sel);
    overlay.appendChild(label);
    overlay.appendChild(mag);
    overlay.appendChild(bar);
    overlay.appendChild(hint);

    /* ---------------- 状态 ---------------- */
    let scale = 1, imgX = 0, imgY = 0, imgW = 0, imgH = 0;
    let phase = "free";              // free | drag | adjust
    let origin = null;               // 拖选起点（原生 px）
    let curPt = null;
    let selRect = null;              // {x1,y1,x2,y2} 原生 px；adjust 阶段持续更新
    let dragMoved = false;
    let snapRect = null;             // 窗口吸附矩形（原生 px）+ 物理坐标缓存
    let snapPhys = null;
    let snapTick = 0;
    let resizeDir = null;            // 调整模式：按中的手柄方向
    let moveOff = null;              // 调整模式：整体移动偏移起点
    let clickSnap = null;            // mousedown 时刻的吸附矩形（单击选窗判断用）
    let finished = false;
    let cancelled = false;

    const snapBox = () => (window.__shotSnapBox || [0, 0]);
    const snapQuery = () => (typeof window.__shotSnapQuery === "function"
      ? window.__shotSnapQuery : null);

    /* ---------------- 几何换算 ---------------- */
    function layout() {
      imgW = img.naturalWidth; imgH = img.naturalHeight;
      // 遮罩 = 屏幕：图片铺满窗口（弹窗尺寸即显示器物理尺寸，scale=1）
      scale = Math.min(window.innerWidth / imgW, window.innerHeight / imgH, 1);
      imgX = Math.round((window.innerWidth - imgW * scale) / 2);
      imgY = Math.round((window.innerHeight - imgH * scale) / 2);
      img.style.width = Math.round(imgW * scale) + "px";
      img.style.height = Math.round(imgH * scale) + "px";
      img.style.left = imgX + "px"; img.style.top = imgY + "px";
      if (isRuler) {
        rc.width = window.innerWidth; rc.height = window.innerHeight;
        rc.style.display = "block";
        hint.textContent = "拖动测量线段（长度/角度）· Enter 清除 · ESC 退出";
      }
    }

    /* ---------------- 标尺模式（ShareX Ruler 内化） ---------------- */
    function drawRuler() {
      rctx.clearRect(0, 0, rc.width, rc.height);
      if (!rulerLine) return;
      const { x1, y1, x2, y2 } = rulerLine;
      const sx1 = x1 * scale + imgX, sy1 = y1 * scale + imgY;
      const sx2 = x2 * scale + imgX, sy2 = y2 * scale + imgY;
      // 线段 + 端点
      rctx.strokeStyle = "#ffc857";
      rctx.lineWidth = 1.5;
      rctx.beginPath(); rctx.moveTo(sx1, sy1); rctx.lineTo(sx2, sy2); rctx.stroke();
      for (const [px, py] of [[sx1, sy1], [sx2, sy2]]) {
        rctx.fillStyle = "#ffc857";
        rctx.beginPath(); rctx.arc(px, py, 3, 0, Math.PI * 2); rctx.fill();
      }
      // 中点读数：长度（原生像素）与角度（水平为 0°，向下为正）
      const dx = x2 - x1, dy = y2 - y1;
      const len = Math.round(Math.hypot(dx, dy));
      const ang = Math.round(Math.atan2(dy, dx) * 180 / Math.PI);
      const mx = (sx1 + sx2) / 2, my = (sy1 + sy2) / 2;
      const text = `${len} px · ${ang}°`;
      rctx.font = "12px Consolas, monospace";
      const tw = rctx.measureText(text).width + 16;
      const bx = mx - tw / 2, by = my - 34;
      rctx.fillStyle = "rgba(16,22,32,.92)";
      rctx.beginPath();
      rctx.roundRect ? rctx.roundRect(bx, by, tw, 22, 5) : rctx.rect(bx, by, tw, 22);
      rctx.fill();
      rctx.strokeStyle = "rgba(255,200,87,.5)"; rctx.lineWidth = 1; rctx.stroke();
      rctx.fillStyle = "#ffe9b0";
      rctx.fillText(text, bx + 8, by + 15);
    }

    function toNative(e) {   // 视口坐标 → 图片原生像素（越界钳位）
      const x = (e.clientX - imgX) / scale;
      const y = (e.clientY - imgY) / scale;
      return [Math.max(0, Math.min(imgW, x)), Math.max(0, Math.min(imgH, y))];
    }

    function physAt(nx, ny) { // 原生像素 → 虚拟桌面物理坐标
      return [snapBox()[0] + nx, snapBox()[1] + ny];
    }

    /* ---------------- 渲染 ---------------- */
    function paintDim(which, x, y, w, h) {
      const d = dims[which];
      d.style.left = x + "px"; d.style.top = y + "px";
      d.style.width = Math.max(0, w) + "px"; d.style.height = Math.max(0, h) + "px";
      d.style.display = "block";
    }

    function hideDims() { dims.forEach((d) => { d.style.display = "none"; }); }

    function renderSel() {
      if (!selRect) { sel.style.display = "none"; label.style.display = "none";
        hideDims(); return; }
      const { x1, y1, x2, y2 } = selRect;
      const sx = x1 * scale + imgX, sy = y1 * scale + imgY;
      const sw = (x2 - x1) * scale, sh = (y2 - y1) * scale;
      sel.style.left = sx + "px"; sel.style.top = sy + "px";
      sel.style.width = sw + "px"; sel.style.height = sh + "px";
      sel.style.display = "block";
      label.textContent = Math.round(x2 - x1) + " × " + Math.round(y2 - y1);
      const lw = label.offsetWidth || 90;
      label.style.left = (sx + sw - lw - 2) + "px";
      label.style.top = Math.max(2, sy - 24) + "px";
      label.style.display = "block";
      paintDim(0, 0, 0, window.innerWidth, sy);
      paintDim(1, 0, sy + sh, window.innerWidth, window.innerHeight - sy - sh);
      paintDim(2, 0, sy, sx, sh);
      paintDim(3, sx + sw, sy, window.innerWidth - sx - sw, sh);
    }

    function renderHandles(show) {
      for (const dir of Object.keys(handles)) {
        const hd = handles[dir];
        if (!show || !selRect) { hd.style.display = "none"; continue; }
        const { x1, y1, x2, y2 } = selRect;
        const px = { n: (x1 + x2) / 2, s: (x1 + x2) / 2, w: x1, e: x2 }[dir];
        const py = { w: (y1 + y2) / 2, e: (y1 + y2) / 2, n: y1, s: y2 }[dir];
        hd.style.left = px * scale + imgX + "px";
        hd.style.top = py * scale + imgY + "px";
        hd.style.display = "block";
      }
    }

    function renderBar() {
      if (phase !== "adjust" || !selRect) { bar.style.display = "none"; return; }
      bar.style.display = "flex";
      const sx = selRect.x2 * scale + imgX, sy = selRect.y2 * scale + imgY;
      const bw = bar.offsetWidth || 240;
      let bx = sx - bw, by = sy + 12;
      if (bx < 8) bx = 8;
      if (by + 40 > window.innerHeight) by = selRect.y1 * scale + imgY - 52;
      bar.style.left = bx + "px"; bar.style.top = by + "px";
    }

    function hideSnap() {
      snapRect = null; snapPhys = null;
      snapHi.style.display = "none"; snapTag.style.display = "none";
    }

    function renderSnap() {
      if (!snapRect) { hideSnap(); return; }
      const sx = snapRect.x1 * scale + imgX, sy = snapRect.y1 * scale + imgY;
      snapHi.style.left = sx + "px"; snapHi.style.top = sy + "px";
      snapHi.style.width = (snapRect.x2 - snapRect.x1) * scale + "px";
      snapHi.style.height = (snapRect.y2 - snapRect.y1) * scale + "px";
      snapHi.style.display = "block";
      snapTag.textContent = "窗口 " + Math.round(snapRect.x2 - snapRect.x1) +
        " × " + Math.round(snapRect.y2 - snapRect.y1) + " · 单击选取";
      snapTag.style.left = sx + "px";
      snapTag.style.top = Math.max(2, sy - 22) + "px";
      snapTag.style.display = "block";
    }

    function updateMag(e) {
      const [x, y] = toNative(e);
      const half = MAG / (2 * ZOOM);
      const sx = Math.max(0, x - half), sy = Math.max(0, y - half);
      const sw = Math.min(imgW - sx, half * 2), sh = Math.min(imgH - sy, half * 2);
      if (sw <= 0 || sh <= 0) { mag.style.display = "none"; return; }
      mctx.imageSmoothingEnabled = false;
      mctx.drawImage(img, sx, sy, sw, sh, 0, 0, MAG, MAG);
      // 底部读数条：中心像素 HEX + 原生坐标（像素级取色辅助）
      const px = mctx.getImageData(MAG / 2 - 1, MAG / 2 - 1, 1, 1).data;
      const hexc = "#" + [px[0], px[1], px[2]]
        .map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase();
      mctx.fillStyle = "rgba(8,12,18,.82)";
      mctx.fillRect(0, MAG - 18, MAG, 18);
      mctx.fillStyle = hexc;
      mctx.fillRect(4, MAG - 14, 10, 10);
      mctx.fillStyle = "#cfe3ff";
      mctx.font = "10px Consolas, monospace";
      mctx.fillText(hexc + "  " + Math.round(x) + "," + Math.round(y), 18, MAG - 6);
      let mx = e.clientX + 24, my = e.clientY - MAG - 16;
      if (mx + MAG > window.innerWidth) mx = e.clientX - MAG - 24;
      if (my < 0) my = e.clientY + 24;
      mag.style.left = mx + "px"; mag.style.top = my + "px";
      mag.style.display = "block";
    }

    /* ---------------- 窗口吸附 ---------------- */
    function querySnap(nx, ny) {
      const q = snapQuery();
      if (!q) return;
      const tick = ++snapTick;
      const [px, py] = physAt(nx, ny);
      Promise.resolve()
        .then(() => q(px, py))
        .then((r) => {
          if (tick !== snapTick || phase !== "free") return;
          if (!r || r.w <= 2 || r.h <= 2) { hideSnap(); return; }
          const bx = snapBox();
          const x1 = Math.max(0, r.x - bx[0]), y1 = Math.max(0, r.y - bx[1]);
          const x2 = Math.min(imgW, r.x - bx[0] + r.w), y2 = Math.min(imgH, r.y - bx[1] + r.h);
          if (x2 - x1 < MIN_SIZE || y2 - y1 < MIN_SIZE) { hideSnap(); return; }
          snapRect = { x1, y1, x2, y2 };
          snapPhys = r;
          renderSnap();
        })
        .catch(() => { /* 遮罩窗口可能已关闭 */ });
    }

    /* ---------------- 交互 ---------------- */
    function handleAt(e) {
      if (phase !== "adjust") return null;
      for (const dir of Object.keys(handles)) {
        const hd = handles[dir];
        if (hd.style.display === "none") continue;
        const r = hd.getBoundingClientRect();
        if (e.clientX >= r.left - 3 && e.clientX <= r.right + 3 &&
            e.clientY >= r.top - 3 && e.clientY <= r.bottom + 3) return dir;
      }
      return null;
    }

    function insideSel(e) {
      if (!selRect) return false;
      const [x, y] = toNative(e);
      return x >= selRect.x1 && x <= selRect.x2 && y >= selRect.y1 && y <= selRect.y2;
    }

    function onDown(e) {
      if (finished || e.button !== 0 || !imgW) return;
      // 工具条按钮（确认/重选/取消）的按下不参与圈选：否则 mousedown 会先把
      // 选区清空重建，随后按钮 click 到达 finish() 时 selRect 已为空而静默失败。
      if (bar.contains(e.target)) return;
      e.preventDefault();
      const [x, y] = toNative(e);
      if (phase === "adjust") {
        const dir = handleAt(e);
        if (dir) { resizeDir = dir; return; }
        if (insideSel(e)) {
          moveOff = { x: x - selRect.x1, y: y - selRect.y1 };
          return;
        }
        // 选区外：重新圈选
        selRect = null;
      }
      phase = "drag";
      dragMoved = false;
      origin = { x, y };
      curPt = { x, y };
      selRect = { x1: x, y1: y, x2: x, y2: y };
      clickSnap = snapRect;   // 保留数据：onUp 据此判定“单击选窗口”
      // 仅隐藏吸附高亮视觉，不ouseout清除数据
      snapHi.style.display = "none";
      snapTag.style.display = "none";
      hint.style.display = "none";
      renderSel();
    }

    function onMove(e) {
      if (finished) return;
      updateMag(e);
      const [x, y] = toNative(e);
      if (isRuler) {
        if (rulerOrigin) {
          rulerLine = { x1: rulerOrigin.x, y1: rulerOrigin.y, x2: x, y2: y };
          drawRuler();
        }
        return;
      }
      if (phase === "free") {
        // 窗口吸附高亮（节流；无吸附钩子时隐藏残留高亮）
        if (!snapQuery()) hideSnap();
        else if (!origin) {
          const now = performance.now();
          if (!onMove._last || now - onMove._last >= SNAP_MS) {
            onMove._last = now;
            querySnap(x, y);
          }
        }
        return;
      }
      if (phase === "drag" && origin) {
        curPt = { x, y };
        if (Math.abs(x - origin.x) >= 1 || Math.abs(y - origin.y) >= 1) dragMoved = true;
        selRect = {
          x1: Math.min(origin.x, x), y1: Math.min(origin.y, y),
          x2: Math.max(origin.x, x), y2: Math.max(origin.y, y),
        };
        renderSel();
        return;
      }
      if (phase === "adjust") {
        if (resizeDir && selRect) {
          const r = { ...selRect };
          if (resizeDir.includes("w")) r.x1 = Math.min(x, r.x2 - MIN_SIZE);
          if (resizeDir.includes("e")) r.x2 = Math.max(x, r.x1 + MIN_SIZE);
          if (resizeDir.includes("n")) r.y1 = Math.min(y, r.y2 - MIN_SIZE);
          if (resizeDir.includes("s")) r.y2 = Math.max(y, r.y1 + MIN_SIZE);
          selRect = r;
          renderSel(); renderHandles(true); renderBar();
        } else if (moveOff && selRect) {
          const w = selRect.x2 - selRect.x1, h = selRect.y2 - selRect.y1;
          let nx1 = x - moveOff.x, ny1 = y - moveOff.y;
          nx1 = Math.max(0, Math.min(imgW - w, nx1));
          ny1 = Math.max(0, Math.min(imgH - h, ny1));
          selRect = { x1: nx1, y1: ny1, x2: nx1 + w, y2: ny1 + h };
          renderSel(); renderHandles(true); renderBar();
        }
      }
    }

    function onUp() {
      if (finished) return;
      if (isRuler) {
        rulerOrigin = null;   // 线段保留（读数常驻），可再拖新线
        return;
      }
      if (phase === "drag" && origin) {
        const w = selRect.x2 - selRect.x1, h = selRect.y2 - selRect.y1;
        // 单击（几乎未拖动）且按下时有窗口吸附 → 选中该窗口并进入调整模式：
        // 不立即完成，避免最大化/全屏窗口被单击误抄成整个屏幕
        if (!dragMoved && clickSnap) {
          selRect = { ...clickSnap };
          clickSnap = null;
          phase = "adjust";
          origin = null;
          hint.style.display = "";
          hint.textContent = "已选中窗口 · Enter / 双击 / 确认完成 · 可拖动或用手柄调整";
          renderSel(); renderHandles(true); renderBar();
          return;
        }
        clickSnap = null;
        if (dragMoved && w >= MIN_SIZE && h >= MIN_SIZE) {
          phase = "adjust";
          origin = null;
          hint.style.display = "none";
          renderSel(); renderHandles(true); renderBar();
          return;
        }
        // 误点：回到空闲
        selRect = null; origin = null; phase = "free";
        hint.style.display = "";
        renderSel(); renderHandles(false); renderBar();
        return;
      }
      resizeDir = null;
      moveOff = null;
    }

    function finish() {
      if (finished || !selRect || !imgW) return;
      const w = Math.round(selRect.x2 - selRect.x1);
      const h = Math.round(selRect.y2 - selRect.y1);
      if (w < 1 || h < 1) return;
      finished = true;
      // 先隐藏遮罩层（结果裁剪来自后端留存 PNG，遮罩内容不会进图）
      overlay.style.display = "none";
      // 进度提示：遮罩隐藏后仍有反馈，避免“黑屏不知道成功没有”
      showStatus("正在生成截图…");
      const x1 = Math.round(selRect.x1), y1 = Math.round(selRect.y1);
      // 前端 canvas 裁剪仅作数据回传；失败（超大图/内存不足）时降级为空串，
      // 后端会从留存 PNG 无损裁剪，不影响最终结果
      let dataUrl = "";
      try {
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        c.getContext("2d").drawImage(img, x1, y1, w, h, 0, 0, w, h);
        dataUrl = c.toDataURL("image/png");
      } catch (e) {
        try { console.warn && console.warn("[RegionCapture] canvas 裁剪降级：", e); } catch (_) {}
        dataUrl = "";
      }
      cleanup(true);   // 保留 ESC 监听：后端无响应时用户仍可按 ESC 退出
      try {
        onRect && onRect(x1, y1, w, h);
        onDone && onDone(dataUrl, w, h, { x: x1, y: y1, w: w, h: h });
      } catch (e) {
        showStatus("提交失败：" + ((e && e.message) || e) + "\n按 ESC 退出");
      }
    }

    function cancel() {
      if (cancelled) return;
      cancelled = true;
      finished = true;   // 阻止提交/重复取消
      hideStatus();
      cleanup();
      onCancel && onCancel();
    }

    function reselect() {
      phase = "free";
      selRect = null;
      hint.style.display = "";
      renderSel(); renderHandles(false); renderBar();
    }

    /* 移除遮罩与鼠标监听；keepKeys=true 时保留 ESC（提交后仍可退出，
       防止后端无响应时界面被“正在生成截图…”永久卡住） */
    function cleanup(keepKeys) {
      overlay.remove();
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      if (!keepKeys) window.removeEventListener("keydown", onKey);
      if (!keepKeys) hideStatus();
    }

    function nudge(dx, dy, step) {
      if (phase !== "adjust" || !selRect) return false;
      const w = selRect.x2 - selRect.x1, h = selRect.y2 - selRect.y1;
      let nx1 = selRect.x1 + dx * step, ny1 = selRect.y1 + dy * step;
      nx1 = Math.max(0, Math.min(imgW - w, nx1));
      ny1 = Math.max(0, Math.min(imgH - h, ny1));
      selRect = { x1: nx1, y1: ny1, x2: nx1 + w, y2: ny1 + h };
      renderSel(); renderHandles(true); renderBar();
      return true;
    }

    function onKey(e) {
      if (e.key === "Enter") finish();
      else if (e.key === "Escape") cancel();
      else if (e.key.startsWith("Arrow")) {
        // 调整模式：方向键微调选区（ShareX 式），Shift = 10px 步进
        const d = { ArrowUp: [0, -1], ArrowDown: [0, 1],
          ArrowLeft: [-1, 0], ArrowRight: [1, 0] }[e.key];
        if (d && nudge(d[0], d[1], e.shiftKey ? 10 : 1)) e.preventDefault();
      }
    }

    okBtn.addEventListener("click", finish);
    reBtn.addEventListener("click", reselect);
    cancelBtn.addEventListener("click", cancel);
    overlay.addEventListener("dblclick", () => { if (!isRuler) finish(); });
    overlay.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove, { passive: true });
    window.addEventListener("mouseup", onUp);
    window.addEventListener("keydown", onKey);

    img.onload = () => layout();
    img.onerror = () => { cleanup(); onCancel && onCancel(); };
    img.src = imgSrc;
  }

  window.RegionCapture = { start };
})();
