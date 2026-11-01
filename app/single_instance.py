# -*- coding: utf-8 -*-
"""单实例守卫（2026-11-01）

问题：重复双击 exe 会再起一整套进程 —— 托盘堆一串图标、同一批 ComfyUI 实例被两套
管理器同时认领，还会互相抢端口/写 config.json。

做法：QLocalServer / QLocalSocket（Windows 下等价命名管道）
  · 新实例先尝试连接已有实例：连上 → 发 "show" → 已有实例把窗口唤到前台 → 新实例退出；
  · 连不上（没有实例，或上次崩溃留下 socket 残留）→ 清残留后自己 listen 当主实例。

为什么不用文件锁：QLockFile 只能判断"有人占着"，没法把已有窗口叫出来；
用户真正要的是"再点一次就把窗口弹到前面"，所以走 IPC。
"""
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

KEY = "ComfyHub-SingleInstance-v1"


class SingleInstance(QObject):
    """`try_acquire()` 返回 True 表示自己是主实例；False 表示已有实例（已通知它显形）"""

    activated = Signal()          # 第二个实例连上来时触发 → 主窗去 show/raise

    def __init__(self, key: str = KEY, parent=None):
        super().__init__(parent)
        self.key = key
        self.server = None

    def try_acquire(self, timeout_ms: int = 500) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        if sock.waitForConnected(timeout_ms):
            # 已有实例在跑：请它把窗口显示出来，然后本进程退出
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(timeout_ms)
            sock.disconnectFromServer()
            return False
        # 没有实例在听 → 清理残留 socket（崩溃后可能留下，导致 listen 失败）
        QLocalServer.removeServer(self.key)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)
        return bool(self.server.listen(self.key))

    def _on_new_connection(self):
        conn = self.server.nextPendingConnection() if self.server else None
        if conn is None:
            return
        conn.disconnected.connect(conn.deleteLater)
        # 连上即视为「把窗口叫出来」，不依赖对方是否真的写了消息
        self.activated.emit()

    def release(self):
        """退出前释放监听，避免下次启动被残留拖住"""
        if self.server is not None:
            self.server.close()
            QLocalServer.removeServer(self.key)
