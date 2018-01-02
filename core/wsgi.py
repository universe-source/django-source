import django
from django.core.handlers.wsgi import WSGIHandler


def get_wsgi_application():
    """
    WSGI: Web Server Gateway Interface, 定义了Web服务器如何与python应用进行交互,
    使得python应用程序可以和web服务器对接.
    WSGIHhandle: 实现 WSGI 协议规范, 返回实现 WSGI 协议的对象.
    The public interface to Django's WSGI support. Return a WSGI callable.

    Avoids making django.core.handlers.WSGIHandler a public API, in case the
    internal WSGI implementation changes or moves in the future.
    """
    django.setup(set_prefix=False)
    return WSGIHandler()
