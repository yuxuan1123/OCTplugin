// home 插件页业务逻辑：打卡 / 任务 / 工作模式 / 网络加速。
// 所有后端调用都经 window.OCT.callPlugin("home", method, params)。
(() => {
  const $ = (id) => document.getElementById(id);
  const toast = (msg) => {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2200);
  };

  const api = (method, params) => window.OCT.callPlugin("home", method, params);

  // ── 打卡 ────────────────────────────
  function renderClockin(c) {
    $("slot-am").classList.toggle("s-done", !!c.am);
    $("slot-pm").classList.toggle("s-done", !!c.pm);
    $("am-state").textContent = c.am ? "已完成" : "未打卡";
    $("pm-state").textContent = c.pm ? "已完成" : "未打卡";
    $("btn-am").textContent = c.am ? "已打卡" : "打卡";
    $("btn-pm").textContent = c.pm ? "已打卡" : "打卡";
    $("btn-am").disabled = !!c.am;
    $("btn-pm").disabled = !!c.pm;
    const hint = $("clock-hint");
    if (c.hint) { hint.textContent = c.hint; hint.classList.add("show"); }
    else hint.classList.remove("show");
  }

  $("btn-am").onclick = async () => {
    try { const r = await api("clockin", { slot: "am" }); toast("上午已打卡 " + (r.time || "").slice(11, 19)); refresh(); }
    catch (e) { toast("打卡失败：" + e.message); }
  };
  $("btn-pm").onclick = async () => {
    try { const r = await api("clockin", { slot: "pm" }); toast("下午已打卡 " + (r.time || "").slice(11, 19)); refresh(); }
    catch (e) { toast("打卡失败：" + e.message); }
  };

  // ── 任务 ────────────────────────────
  function renderTasks(tasks) {
    const ul = $("task-list"); ul.innerHTML = "";
    if (!tasks || !tasks.length) {
      const li = document.createElement("li");
      li.innerHTML = '<span class="txt" style="color:#999">暂无任务</span>';
      ul.appendChild(li); return;
    }
    tasks.forEach((t) => {
      const li = document.createElement("li");
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = !!t.done;
      cb.addEventListener("change", () => api("task.toggle", { id: t.id, done: cb.checked }).then(refresh).catch(() => 0));
      const span = document.createElement("span");
      span.className = "txt" + (t.done ? " done" : "");
      span.textContent = t.text;
      const del = document.createElement("button");
      del.className = "del"; del.textContent = "×"; del.title = "删除";
      del.onclick = () => api("task.remove", { id: t.id }).then(refresh).catch(() => 0);
      li.append(cb, span, del); ul.appendChild(li);
    });
  }

  function addTask() {
    const input = $("task-input"); const text = input.value.trim();
    if (!text) return;
    api("task.add", { text }).then(() => { input.value = ""; refresh(); }).catch((e) => toast("添加失败：" + e.message));
  }
  $("btn-task-add").onclick = addTask;
  $("task-input").addEventListener("keydown", (e) => { if (e.key === "Enter") addTask(); });

  // ── 工作模式 ────────────────────────
  $("btn-workmode").onclick = async () => {
    const btn = $("btn-workmode"); btn.disabled = true; btn.textContent = "启动中…";
    try {
      const r = await api("workmode");
      toast(r.ok ? "工作模式已启动" : "启动失败：" + (r.error || ""));
    } catch (e) { toast("启动失败：" + e.message); }
    btn.disabled = false; btn.textContent = "启动工作模式脚本";
  };

  // ── 网络加速 ────────────────────────
  function renderNet(state) {
    const led = $("net-led"); const txt = $("net-txt"); const btn = $("btn-net");
    led.className = "led " + state;
    txt.textContent = state === "on" ? "已开启" : state === "off" ? "已关闭" : "未知";
    btn.textContent = state === "on" ? "关闭加速" : "开启加速";
    btn.disabled = state === "unknown";
  }
  $("btn-net").onclick = async () => {
    const btn = $("btn-net"); btn.disabled = true;
    try {
      const st = $("net-led").className; const wantOn = st.indexOf("on") < 0;
      const r = await api("net.toggle", { on: wantOn });
      if (!r.ok) { toast("切换失败：" + (r.error || "")); }
      setTimeout(refresh, 900);
    } catch (e) { toast("切换失败：" + e.message); }
  };

  // ── 首次加载 + 刷新 ─────────────────
  async function refresh() {
    try {
      const r = await api("state");
      renderClockin(r.clockin);
      renderTasks(r.tasks);
      renderNet(r.net || "unknown");
    } catch (e) { toast((e.message || "加载失败") + "（桥未就绪）"); }
  }

  window.addEventListener("oct.ready", () => { refresh(); });
})();