from django.utils.version import get_version

VERSION = (2, 0, 0, 'final', 0)

__version__ = get_version(VERSION)


def setup(set_prefix=True):
    """
    Configure the settings (this happens as a side effect of accessing the
    first setting), configure logging and populate the app registry.
    Set the thread-local urlresolvers script prefix if `set_prefix` is True.
    1 配置Django
        加载配置;
        设置日志;
        初始化应用注册表
    2 自动调用该函数
        运行一个通过 DJANGO的 WSGI 支持的HTTP 服务
        调用管理命令
    """
    # 0 加载配置
    from django.apps import apps
    from django.conf import settings
    from django.urls import set_script_prefix
    from django.utils.log import configure_logging

    # 1 配置日志(导入dictConfig模块, 载入自定义配置)
    configure_logging(settings.LOGGING_CONFIG, settings.LOGGING)
    if set_prefix:
        set_script_prefix(
            '/' if settings.FORCE_SCRIPT_NAME is None else settings.FORCE_SCRIPT_NAME
        )

    # 2 初始化apps, 遍历调用AppConfig.create()
    apps.populate(settings.INSTALLED_APPS)
