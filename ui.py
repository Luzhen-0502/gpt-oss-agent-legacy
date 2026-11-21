# -*- coding: utf-8 -*-
"""
ui.py —— 中文图形界面（Tkinter，大肥鱼手写）

布局：
    ┌──────────────────────────────────────────────┐
    │ 顶部：标题 + 当前模型状态                      │
    ├───────────────────────┬──────────────────────┤
    │ 左侧：聊天记录         │ 右侧：图片预览        │
    │ （谁说了什么、         │ （上传的原图 /        │
    │   调了什么工具、       │   生成出来的新图）    │
    │   工具返回了什么）     │ + 图片中文描述        │
    ├───────────────────────┴──────────────────────┤
    │ 底部：输入框 + 【上传图片】【开始生成】【停止】 │
    └──────────────────────────────────────────────┘

线程模型（很重要）：
    推理在后台线程跑，绝对不能在后台线程里直接碰 Tk 控件（会闪退）。
    所以所有事件都走 queue.Queue，主线程用 after() 轮询取出来再更新界面。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from config import Config, check_model_files

log = logging.getLogger("gptoss.ui")

try:
    from PIL import Image, ImageTk  # type: ignore

    HAS_PIL = True
except Exception:  # noqa: BLE001
    HAS_PIL = False

from agent_core import AgentCore
from memory import SharedMemory
from tools import ToolRegistry, build_default_registry

# 工具英文名 → 界面上显示的中文名
TOOL_NAMES_ZH = {
    "see_image": "看图理解",
    "generate_image": "生成图片",
    "search_knowledge": "联网检索",
    "get_current_time": "获取时间",
}

FONT = "Microsoft YaHei UI"


def tool_zh(name: str) -> str:
    return TOOL_NAMES_ZH.get(name, name)


class AgentApp:
    """主窗口。"""

    def __init__(
        self,
        cfg: Config,
        memory: SharedMemory,
        loader: Any,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.cfg = cfg
        self.memory = memory
        self.loader = loader
        self.registry = registry or build_default_registry()

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.core = AgentCore(
            cfg=cfg,
            memory=memory,
            loader=loader,
            registry=self.registry,
            emit=self._emit,
            confirm=self._ask_confirm,
        )

        self.busy = False
        self.pending_image = ""
        self._photo: Any = None
        self._pil_image: Any = None
        self._resize_job: str | None = None
        self._network_warned_this_turn = False
        self._worker: threading.Thread | None = None
        # 危险操作的人工确认：worker 线程等这个 Event，主线程弹窗后 set
        self._confirm_event: threading.Event | None = None
        self._confirm_answer: bool = False

        self._build_window()
        self._build_widgets()
        self._post_welcome()

        # 模型管理器的状态回调也走队列（它在后台线程里被调用）
        try:
            self.loader.set_status_callback(self._on_model_status)
        except Exception:
            pass

        self.root.after(80, self._poll_events)

    # ==================================================================
    # 一、界面搭建
    # ==================================================================
    def _build_window(self) -> None:
        self.root = tk.Tk()
        self.root.title(str(self.cfg.get("app.title", "GPT-OSS 专属 Agent")))
        size = self.cfg.get("app.window_size", [1280, 820])
        try:
            w, h = int(size[0]), int(size[1])
        except Exception:
            w, h = 1280, 820
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(980, 640)

        # 窗口居中
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")

        fs = int(self.cfg.get("app.font_size", 11))
        self.fs = fs
        self.font_normal = (FONT, fs)
        self.font_bold = (FONT, fs, "bold")
        self.font_small = (FONT, max(8, fs - 1))
        self.font_title = (FONT, fs + 3, "bold")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self) -> None:
        root = self.root
        root.configure(bg="#f5f6f8")

        # ---------------- 顶部标题栏 ----------------
        top = tk.Frame(root, bg="#ffffff", height=56)
        top.pack(side=tk.TOP, fill=tk.X)
        top.pack_propagate(False)

        tk.Label(
            top,
            text="🐟 GPT-OSS 专属 Agent",
            font=self.font_title,
            bg="#ffffff",
            fg="#1f2937",
        ).pack(side=tk.LEFT, padx=16)

        tk.Label(
            top,
            text="看图 · 查资料 · 写提示词 · 出图",
            font=self.font_small,
            bg="#ffffff",
            fg="#9ca3af",
        ).pack(side=tk.LEFT, padx=4)

        self.model_status_var = tk.StringVar(value="模型未加载")
        tk.Label(
            top,
            textvariable=self.model_status_var,
            font=self.font_small,
            bg="#ffffff",
            fg="#6b7280",
        ).pack(side=tk.RIGHT, padx=16)

        # 一条分隔线
        tk.Frame(root, bg="#e5e7eb", height=1).pack(side=tk.TOP, fill=tk.X)

        # ---------------- 中间左右分栏 ----------------
        paned = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(8, 0))

        # --- 左：聊天记录 ---
        left = tk.Frame(paned, bg="#ffffff")
        paned.add(left, weight=3)

        tk.Label(
            left, text="对话记录", font=self.font_bold, bg="#ffffff", fg="#374151", anchor="w"
        ).pack(side=tk.TOP, fill=tk.X, padx=12, pady=(8, 4))

        chat_wrap = tk.Frame(left, bg="#ffffff")
        chat_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.chat = tk.Text(
            chat_wrap,
            wrap=tk.WORD,
            font=self.font_normal,
            bg="#ffffff",
            fg="#111827",
            relief=tk.FLAT,
            padx=10,
            pady=10,
            spacing1=2,
            spacing3=6,
            state=tk.DISABLED,
            cursor="arrow",
        )
        chat_scroll = ttk.Scrollbar(chat_wrap, orient=tk.VERTICAL, command=self.chat.yview)
        self.chat.configure(yscrollcommand=chat_scroll.set)
        chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.chat.tag_configure("user", foreground="#1d4ed8", font=self.font_bold)
        self.chat.tag_configure("assistant", foreground="#111827")
        self.chat.tag_configure("tool", foreground="#047857")
        self.chat.tag_configure("result", foreground="#4b5563")
        self.chat.tag_configure("system", foreground="#9ca3af", font=self.font_small)
        self.chat.tag_configure("error", foreground="#b91c1c", font=self.font_bold)
        self.chat.tag_configure("warn", foreground="#b45309")
        self.chat.tag_configure("thinking", foreground="#6b7280")
        self.chat.tag_configure("title", foreground="#111827", font=self.font_bold)

        # --- 右：图片预览 ---
        right = tk.Frame(paned, bg="#ffffff")
        paned.add(right, weight=2)

        head = tk.Frame(right, bg="#ffffff")
        head.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(8, 4))
        tk.Label(head, text="图片预览", font=self.font_bold, bg="#ffffff", fg="#374151").pack(side=tk.LEFT)
        self.image_info_var = tk.StringVar(value="")
        tk.Label(
            head, textvariable=self.image_info_var, font=self.font_small, bg="#ffffff", fg="#9ca3af"
        ).pack(side=tk.RIGHT)

        self.canvas = tk.Canvas(right, bg="#f3f4f6", highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        # 图片说明小面板
        desc_wrap = tk.Frame(right, bg="#ffffff")
        desc_wrap.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 8))
        tk.Label(
            desc_wrap, text="图片说明（视觉模型写进共享记忆的中文描述）",
            font=self.font_small, bg="#ffffff", fg="#6b7280", anchor="w",
        ).pack(side=tk.TOP, fill=tk.X)
        self.desc_text = tk.Text(
            desc_wrap, height=4, wrap=tk.WORD, font=self.font_small,
            bg="#f9fafb", fg="#374151", relief=tk.FLAT, padx=8, pady=6,
            state=tk.DISABLED, cursor="arrow",
        )
        self.desc_text.pack(side=tk.TOP, fill=tk.X, pady=(2, 0))

        # ---------------- 底部输入区 ----------------
        bottom = tk.Frame(root, bg="#f5f6f8")
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=8)

        entry_wrap = tk.Frame(bottom, bg="#ffffff", highlightbackground="#e5e7eb", highlightthickness=1)
        entry_wrap.pack(side=tk.TOP, fill=tk.X)

        self.input = tk.Text(
            entry_wrap,
            height=3,
            wrap=tk.WORD,
            font=self.font_normal,
            bg="#ffffff",
            fg="#111827",
            relief=tk.FLAT,
            padx=10,
            pady=8,
        )
        self.input.pack(side=tk.TOP, fill=tk.X)
        self.input.bind("<Return>", self._on_enter)
        self.input.bind("<Shift-Return>", lambda e: None)  # Shift+回车换行
        self.input.focus_set()

        btn_row = tk.Frame(bottom, bg="#f5f6f8")
        btn_row.pack(side=tk.TOP, fill=tk.X, pady=(8, 0))

        self.btn_upload = tk.Button(
            btn_row, text="📁 上传图片", font=self.font_normal,
            bg="#ffffff", fg="#374151", relief=tk.FLAT, padx=16, pady=6,
            command=self._on_upload, cursor="hand2", activebackground="#f3f4f6",
        )
        self.btn_upload.pack(side=tk.LEFT)

        self.btn_generate = tk.Button(
            btn_row, text="✨ 开始生成", font=self.font_bold,
            bg="#1d4ed8", fg="#ffffff", relief=tk.FLAT, padx=22, pady=6,
            command=self._on_start, cursor="hand2", activebackground="#1e40af",
            activeforeground="#ffffff",
        )
        self.btn_generate.pack(side=tk.LEFT, padx=10)

        self.btn_stop = tk.Button(
            btn_row, text="■ 停止", font=self.font_normal,
            bg="#e5e7eb", fg="#6b7280", relief=tk.FLAT, padx=14, pady=6,
            command=self._on_stop, cursor="hand2", state=tk.DISABLED,
        )
        self.btn_stop.pack(side=tk.LEFT)

        tk.Label(
            btn_row,
            text="Enter 发送 · Shift+Enter 换行",
            font=self.font_small, bg="#f5f6f8", fg="#9ca3af",
        ).pack(side=tk.RIGHT)

        # ---------------- 状态栏 ----------------
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(
            root, textvariable=self.status_var, font=self.font_small,
            bg="#f5f6f8", fg="#6b7280", anchor="w",
        ).pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(0, 4))

    # ==================================================================
    # 二、启动欢迎信息 + 环境体检
    # ==================================================================
    def _post_welcome(self) -> None:
        self._append("🐟 大肥鱼给你搭的专属 Agent 已就位。\n", "title")
        self._append("本鱼会这些活：\n", "system")
        try:
            for line in self.registry.describe_for_human().splitlines():
                self._append(f"  {line}\n", "system")
        except Exception:
            pass
        self._append(
            "\n用法的三种姿势：\n"
            "  ① 直接打字：「帮我生成一张赛博朋克风格的猫」\n"
            "  ② 先上传图片再点开始：我会先看懂图，再据此生成新图\n"
            "  ③ 上传图 + 文字要求：「把这张图改成水彩风格」\n",
            "system",
        )
        self._append(
            "提示：本机是纯 CPU 跑 20B 模型，第一次生成要先加载模型（十几 GB），"
            "等得久是正常的，不是卡死了。\n\n",
            "system",
        )

        # 模型文件体检：缺什么就说清楚（不阻止启动，方便用户先看界面）
        problems = check_model_files(self.cfg)
        if problems:
            lines = ["【模型文件缺失】以下文件没找到：\n"]
            for p in problems:
                lines.append(f"· {p['title']}\n  路径：{p['path']}\n  怎么办：{p['hint']}\n")
            text = "".join(lines)
            self._append(text, "warn")
            # 延迟弹窗，先让窗口画出来
            self.root.after(400, lambda: messagebox.showwarning(
                "模型文件缺失",
                f"有 {len(problems)} 个模型文件没找到，先把文件放好再点「开始生成」。\n\n"
                + "\n".join(f"· {p['title']}" for p in problems)
                + "\n\n具体路径和下载说明已写在左侧对话区，也可以看 models/README.md。",
            ))

    # ==================================================================
    # 三、聊天区写入
    # ==================================================================
    def _append(self, text: str, tag: str = "assistant") -> None:
        self.chat.configure(state=tk.NORMAL)
        self.chat.insert(tk.END, text, tag)
        # 太长了就删掉最前面的，避免内存和卡顿
        try:
            line_count = int(self.chat.index("end-1c").split(".")[0])
            if line_count > 4000:
                self.chat.delete("1.0", f"{line_count - 3000}.0")
        except Exception:
            pass
        self.chat.configure(state=tk.DISABLED)
        self.chat.see(tk.END)

    def _append_block(self, title: str, body: str, title_tag: str, body_tag: str) -> None:
        self._append(title, title_tag)
        self._append(body + "\n\n", body_tag)

    # ==================================================================
    # 四、事件桥：后台线程 → 队列 → 主线程
    # ==================================================================
    def _emit(self, kind: str, payload: Any = None) -> None:
        """给 AgentCore / 工具层用的线程安全发射口。"""
        self.events.put((kind, payload))

    def _on_model_status(self, msg: str) -> None:
        self.events.put(("status", msg))

    # ------------------------------------------------------------------
    # 危险操作的人工确认（worker 线程会阻塞等这里）
    # ------------------------------------------------------------------
    def _ask_confirm(self, message: str) -> bool:
        """在 worker 线程里被工具调用：把问题丢给主线程弹窗，等用户点。"""
        self._confirm_event = threading.Event()
        self._confirm_answer = False
        self.events.put(("ask_confirm", message))
        got = self._confirm_event.wait(timeout=300)
        self._confirm_event = None
        if not got:
            return False
        return bool(self._confirm_answer)

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                try:
                    self._handle_event(kind, payload)
                except Exception:
                    log.exception("处理事件 %s 出错", kind)
        except queue.Empty:
            pass
        except Exception:
            log.exception("事件轮询出错")
        self.root.after(80, self._poll_events)

    def _handle_event(self, kind: str, payload: Any) -> None:
        if kind == "user":
            self._append_block("👤 你：", str(payload), "user", "assistant")

        elif kind == "thinking":
            text = str(payload).strip()
            if text:
                if len(text) > 400:
                    text = text[:400] + "……"
                self._append(f"💭 思考：{text}\n\n", "thinking")

        elif kind == "narrate":
            self._append(f"🤖 {payload}\n\n", "assistant")

        elif kind == "tool_call":
            name = str(payload.get("name", ""))
            args = payload.get("args") or {}
            self._append(f"🔧 调用工具「{tool_zh(name)}」", "tool")
            if args:
                brief = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
                if len(brief) > 160:
                    brief = brief[:160] + "…"
                self._append(f"（{brief}）", "result")
            self._append("\n", "tool")
            self.status_var.set(f"正在执行：{tool_zh(name)}")

        elif kind == "tool_result":
            name = str(payload.get("name", ""))
            result = str(payload.get("result", ""))
            brief = result.strip().replace("\n", " ")
            if len(brief) > 320:
                brief = brief[:320] + "……"
            self._append(f"📎 工具返回：{brief}\n\n", "result")

        elif kind == "status":
            msg = str(payload)
            self.status_var.set(msg)

        elif kind == "progress":
            try:
                step, total = payload
                self.status_var.set(f"推理中：第 {step}/{total} 步")
            except Exception:
                pass

        elif kind == "retry":
            self._append(f"⚠️ {payload}\n\n", "warn")
            self.status_var.set(str(payload))

        elif kind == "degrade":
            self._append(f"⚠️ {payload}\n\n", "warn")

        elif kind == "vision_done":
            desc = str((payload or {}).get("description", ""))
            self._set_desc(desc)

        elif kind == "knowledge_done":
            info = payload or {}
            title = str(info.get("title", ""))
            summary = str(info.get("summary", ""))
            if len(summary) > 200:
                summary = summary[:200] + "……"
            self._append(f"🌐 查到「{title}」：{summary}\n\n", "result")

        elif kind == "image_ready":
            path = str(payload)
            self._show_image(path, source="生成")
            self._append("🖼 图片已生成，看右边预览。\n\n", "tool")

        elif kind == "network_error":
            msg = str(payload)
            self._append(f"📵 网络问题：{msg}\n\n", "error")
            if not self._network_warned_this_turn:
                self._network_warned_this_turn = True
                messagebox.showwarning(
                    "没有网络",
                    "连不上网，这次没法查资料。\n\n"
                    "我会用自己知道的知识继续画图，"
                    "但冷门风格可能画得不够准。\n\n"
                    f"详细信息：{msg}",
                )

        elif kind == "final":
            text = str(payload)
            self._append_block("🤖 助手：", text, "assistant", "assistant")
            self.status_var.set("完成")

        elif kind == "ask_confirm":
            msg = str(payload)
            self.status_var.set("等你确认一个危险操作……")
            answer = messagebox.askyesno("⚠️ 需要你确认", msg)
            self._confirm_answer = bool(answer)
            ev = self._confirm_event
            if ev is not None:
                ev.set()
            if not answer:
                self._append("⛔ 你拒绝了这个操作。\n\n", "warn")

        elif kind == "error":
            msg = str(payload)
            self._append_block("❌ 出错了：", msg, "error", "error")
            self.status_var.set("出错")
            messagebox.showerror("出错了", msg)

        elif kind == "done":
            self._set_busy(False)

        elif kind == "cancelled":
            self._append("⏹ 任务已取消。\n\n", "warn")
            self._set_busy(False)

        elif kind == "exception":
            msg = str(payload)
            self._append_block("❌ 出错了：", msg, "error", "error")
            self._set_busy(False)
            messagebox.showerror("出错了", msg)

    # ==================================================================
    # 五、图片预览
    # ==================================================================
    def _show_image(self, path: str, source: str = "") -> None:
        if not HAS_PIL:
            self.image_info_var.set("缺少 Pillow，无法预览")
            return
        try:
            img = Image.open(path)
            self._pil_image = img.copy()
            img.close()
        except Exception as e:  # noqa: BLE001
            log.exception("打开图片失败")
            messagebox.showerror("图片打不开", f"这张图片打不开：\n{path}\n\n{e}")
            return

        name = Path(path).name
        self.image_info_var.set(f"{source}：{name}" if source else name)
        self._render_canvas()

        # 右侧说明面板：优先用记忆里的中文描述
        rec = self.memory.image_by_path(path)
        if rec:
            text = str(rec.get("description", ""))
            if rec.get("prompt"):
                text = f"{text}\n\n【提示词】{rec['prompt']}"
            self._set_desc(text)

    def _set_desc(self, text: str) -> None:
        self.desc_text.configure(state=tk.NORMAL)
        self.desc_text.delete("1.0", tk.END)
        self.desc_text.insert("1.0", text.strip())
        self.desc_text.configure(state=tk.DISABLED)

    def _on_canvas_resize(self, _event: Any = None) -> None:
        # 窗口拖动时别每一帧都重算，防抖 120ms
        if self._resize_job is not None:
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(120, self._render_canvas)

    def _render_canvas(self) -> None:
        if not HAS_PIL or self._pil_image is None:
            return
        self._resize_job = None
        cw = max(50, self.canvas.winfo_width())
        ch = max(50, self.canvas.winfo_height())
        try:
            img = self._pil_image.copy()
            img.thumbnail((cw - 16, ch - 16), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(cw // 2, ch // 2, image=self._photo)
        except Exception:
            log.exception("渲染图片失败")

    # ==================================================================
    # 六、按钮与输入
    # ==================================================================
    def _on_enter(self, event: Any) -> str:
        if event.state & 0x0001:  # Shift 按住
            return ""
        self._on_start()
        return "break"

    def _on_upload(self) -> None:
        path = filedialog.askopenfilename(
            title="选择一张图片",
            filetypes=[
                ("图片文件", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self.pending_image = path
        self._show_image(path, source="已上传")
        self.memory.set_task(pending_image=path)
        self._append(f"📁 已选择图片：{Path(path).name}\n", "system")
        self._append("   点「开始生成」我就会先看懂它，再按你的要求改图/出新图。\n\n", "system")
        self.status_var.set("图片已就绪")

    def _on_stop(self) -> None:
        self.core.cancel()
        self.btn_stop.configure(state=tk.DISABLED)
        self.status_var.set("正在取消（要等当前这一步算完）……")

    def _on_start(self) -> None:
        if self.busy:
            return
        text = self.input.get("1.0", tk.END).strip()
        image = self.pending_image
        if not text and not image:
            messagebox.showinfo("还没说要做什么", "先在下面输入框写一句要求，或者点【上传图片】选一张图。")
            return

        if not HAS_PIL:
            messagebox.showerror("缺少依赖", "没有安装 Pillow，图片功能用不了。\n请先执行：pip install Pillow")
            return

        self.input.delete("1.0", tk.END)
        self.pending_image = ""
        self._network_warned_this_turn = False
        self._set_busy(True)
        self.status_var.set("准备开始……")

        # 图片路径先记进记忆，工具里能直接取到
        if image:
            self.memory.set_task(pending_image=image)

        self._worker = threading.Thread(
            target=self._run_worker, args=(text, image), daemon=True, name="agent-worker"
        )
        self._worker.start()

    def _run_worker(self, text: str, image: str) -> None:
        try:
            self.core.run(text, image or "")
            self.events.put(("done", None))
        except Exception as e:  # noqa: BLE001
            from agent_core import AgentCancelled

            if isinstance(e, AgentCancelled):
                self.events.put(("cancelled", None))
                return
            log.exception("Agent 运行失败")
            self.events.put(("exception", f"{type(e).__name__}: {e}"))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        try:
            self.btn_generate.configure(state=state, bg="#9ca3af" if busy else "#1d4ed8")
            self.btn_upload.configure(state=state)
            self.btn_stop.configure(state=tk.NORMAL if busy else tk.DISABLED,
                                    fg="#b91c1c" if busy else "#6b7280")
        except Exception:
            pass
        if not busy:
            self.status_var.set("就绪")

    # ==================================================================
    # 七、关闭
    # ==================================================================
    def _on_close(self) -> None:
        try:
            self.core.cancel()
        except Exception:
            pass
        try:
            self.memory.save()
        except Exception:
            pass
        try:
            self.loader.shutdown()
        except Exception:
            log.exception("关闭模型管理器失败")
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# ===========================================================================
# 冒烟测试：python ui.py --smoke
# ===========================================================================
if __name__ == "__main__":
    import sys
    import tempfile

    logging.basicConfig(level=logging.INFO)
    from config import Config as Cfg
    from loader import ModelManager
    from memory import SharedMemory as Mem

    c = Cfg()
    tmp_mem = Mem(Path(tempfile.mkdtemp()) / "mem.json")
    mm = ModelManager(c)
    app = AgentApp(c, tmp_mem, mm, build_default_registry())
    if "--smoke" in sys.argv:
        app.root.after(1200, app.root.destroy)
    app.run()
