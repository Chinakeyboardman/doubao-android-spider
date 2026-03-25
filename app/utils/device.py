import uiautomator2 as u2

class DeviceManager:
    """
    设备管理模块
    """
    
    def __init__(self, device_id=None):
        self.device_id = device_id
        self.device = None
    
    def connect(self):
        """
        连接设备
        """
        try:
            if self.device_id:
                self.device = u2.connect(self.device_id)
            else:
                self.device = u2.connect()
            print("✅ 设备连接成功")
            return True
        except Exception as e:
            print(f"❌ 设备连接失败: {e}")
            return False
    
    def get_device(self):
        """
        获取设备实例
        """
        if not self.device:
            self.connect()
        return self.device
    
    def app_start(self, package_name, stop=False):
        """
        启动应用
        """
        return self.device.app_start(package_name, stop=stop)
    
    def app_stop(self, package_name):
        """
        停止应用
        """
        return self.device.app_stop(package_name)
    
    def shell(self, command):
        """
        执行shell命令
        """
        return self.device.shell(command)
    
    def swipe(self, *args, **kwargs):
        """
        滑动操作
        """
        return self.device.swipe(*args, **kwargs)
    
    def screenshot(self, path):
        """
        截图
        """
        return self.device.screenshot(path)
    
    def xpath(self, selector):
        """
        XPath选择器
        """
        return self.device.xpath(selector)
