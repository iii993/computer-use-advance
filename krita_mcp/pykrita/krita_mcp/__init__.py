"""Krita MCP bridge -- exposes Krita to an MCP client over loopback HTTP."""

import os
import time
import traceback

from krita import Krita


def boot_log(message):
    """启动阶段的落盘日志。

    Krita 是无控制台的 GUI 进程, Python 的 print/traceback 会被丢掉, 所以
    "插件到底加载到哪一步"必须写文件才能排查。路径与 bridge.json 同目录
    (%APPDATA%\\krita\\krita_mcp_boot.log, 可用 KRITA_MCP_STATE_DIR 覆盖)。
    """
    try:
        base = os.environ.get("KRITA_MCP_STATE_DIR") or os.path.join(
            os.environ.get("APPDATA") or os.path.expanduser("~"), "krita")
        with open(os.path.join(base, "krita_mcp_boot.log"), "a", encoding="utf-8") as fh:
            fh.write("%s  [krita pid %s] %s\n"
                     % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), message))
    except Exception:
        pass


boot_log("__init__ import start")

try:
    from .extension import KritaMcpExtension
    boot_log("extension imported")
    Krita.instance().addExtension(KritaMcpExtension(Krita.instance()))
    boot_log("addExtension ok")
except Exception:
    boot_log("FAILED in import/addExtension:\n" + traceback.format_exc())
    raise
