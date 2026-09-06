"""模式基类"""
from utils.logger import get_logger


class BaseMode:
    name = "base"
    display = "基础模式"

    def __init__(self, app=None):
        self.app = app
        self.log = get_logger(self.name)

    def on_enable(self):
        """切换到本模式时调用"""
        self.log.info("%s 已启用", self.display)

    def on_disable(self):
        """离开本模式时调用"""
        pass

    def on_hotkey(self, key: str):
        """处理本模式热键(在 main 注册的全局热键触发)"""
        pass
