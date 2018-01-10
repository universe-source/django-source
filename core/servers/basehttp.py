"""
HTTP server that implements the Python WSGI protocol (PEP 333, rev 1.21).

Based on wsgiref.simple_server which is part of the standard library since 2.5.

This is a simple server for use in testing or debugging Django apps. It hasn't
been reviewed for security issues. DON'T USE IT FOR PRODUCTION USE!
"""

import logging
import socket
import socketserver
import sys
from wsgiref import simple_server

from django.core.exceptions import ImproperlyConfigured
from django.core.wsgi import get_wsgi_application
from django.utils.module_loading import import_string

__all__ = ('WSGIServer', 'WSGIRequestHandler')

logger = logging.getLogger('django.server')


def get_internal_wsgi_application():
    """
    Load and return the WSGI application as configured by the user in
    ``settings.WSGI_APPLICATION``. With the default ``startproject`` layout,
    this will be the ``application`` object in ``projectname/wsgi.py``.

    This function, and the ``WSGI_APPLICATION`` setting itself, are only useful
    for Django's internal server (runserver); external WSGI servers should just
    be configured to point to the correct application object directly.

    If settings.WSGI_APPLICATION is not set (is ``None``), return
    whatever ``django.core.wsgi.get_wsgi_application`` returns.
    """
    # 1 默认, 例如: unusebamboo.wsgi.application
    from django.conf import settings
    app_path = getattr(settings, 'WSGI_APPLICATION')
    if app_path is None:
        return get_wsgi_application()

    # 2 即: manage.py runserver实际上仍旧使用到了wsgi服务
    #   实际上作为一个"钩子", 在调用get_wsgi_application之前, 进行用户自定义的配置
    #   具体见: unusebamboo/wsgi.py文件
    try:
        # 导入wsgi.py并返回get_wsgi_application函数对象
        return import_string(app_path)
    except ImportError as err:
        print('Error:', err)
        raise ImproperlyConfigured(
            "WSGI application '%s' could not be loaded; "
            "Error importing module." % app_path
        ) from err


def is_broken_pipe_error():
    exc_type, exc_value = sys.exc_info()[:2]
    return issubclass(exc_type, socket.error) and exc_value.args[0] == 32


class WSGIServer(simple_server.WSGIServer):
    """BaseHTTPServer that implements the Python WSGI protocol
    继承关系: WSGIServer-->HTTPServer-->TCPServer-->BaseServer
    """

    request_queue_size = 10

    def __init__(self, *args, **kwargs):
        ipv6 = kwargs.pop('ipv6', False)
        allow_reuse_address = kwargs.pop('allow_reuse_address', True)
        if ipv6:
            self.address_family = socket.AF_INET6
        self.allow_reuse_address = allow_reuse_address
        # 在socketserver.TCPServer中:创建信号量, 创建 TCP 套接字, 绑定地址和端口, 监听
        super().__init__(*args, **kwargs)

    def handle_error(self, request, client_address):
        if is_broken_pipe_error():
            logger.info("- Broken pipe from %s\n", client_address)
        else:
            super().handle_error(request, client_address)


class ThreadedWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    """A threaded version of the WSGIServer"""
    pass


class ServerHandler(simple_server.ServerHandler):
    http_version = '1.1'

    def handle_error(self):
        # Ignore broken pipe errors, otherwise pass on
        if not is_broken_pipe_error():
            super().handle_error()


class WSGIRequestHandler(simple_server.WSGIRequestHandler):
    """
    1 利用 WSGIServer 进行socket监听, 对于每一个连接, 最终会调用该类进行处理.
    2 wsgiref(WSGIRequestHandler)->httpserver(BaseHTTPRequestHandler)
        ->socketserver(StreamRequestHandler)->BaseRequestHandler
    其中BaseRequesthandler实例化逻辑:
        setup(): 在StreamRequestHandler中被重写
            connection: socket request;
            server: 传入的WSGIServer实例本身(藏有 WSGI Application);
            rfile/wfile: file decriptor;
        handle(): 在 WSGIRequestHandler中被重写
            利用rfile读取请求数据;
            parse_request解析;
            实例化ServerHandler, 在run中导入WSGIRequestHandler和WSGI Application;
        finish(): 在StreamRequestHandler中被重写
            close wfile and rfile, 表示服务器关闭连接(进入四次握手)
    """
    protocol_version = 'HTTP/1.1'

    def address_string(self):
        # Short-circuit parent method to not call socket.getfqdn
        return self.client_address[0]

    def log_message(self, formats, *args):
        extra = {
            'request': self.request,
            'server_time': self.log_date_time_string(),
        }
        if args[1][0] == '4':
            # 0x16 = Handshake, 0x03 = SSL 3.0 or TLS 1.x
            if args[0].startswith('\x16\x03'):
                extra['status_code'] = 500
                logger.error(
                    "You're accessing the development server over HTTPS, but "
                    "it only supports HTTP.\n", extra=extra,
                )
                return

        if args[1].isdigit() and len(args[1]) == 3:
            status_code = int(args[1])
            extra['status_code'] = status_code

            if status_code >= 500:
                level = logger.error
            elif status_code >= 400:
                level = logger.warning
            else:
                level = logger.info
        else:
            level = logger.info

        level(formats, *args, extra=extra)

    def get_environ(self):
        # Strip all headers with underscores in the name before constructing
        # the WSGI environ. This prevents header-spoofing based on ambiguity
        # between underscores and dashes both normalized to underscores in WSGI
        # env vars. Nginx and Apache 2.4+ both do this as well.
        for k, _ in self.headers.items():
            if '_' in k:
                del self.headers[k]

        return super().get_environ()

    def handle(self):
        """Copy of WSGIRequestHandler.handle() but with different ServerHandler
        Note: 该方法在对象被实例化的同时调用, 继承体系:
            WSGIRequestHandler-->simple_server.WSGIRequestHandler
                              -->BaseHTTPRequestHandler
                              -->SocketServer.StreamRequestHandler
                              -->BaseRequestHandler
        """
        # 1 利用rfile/wfile读写数据
        self.raw_requestline = self.rfile.readline(65537)
        if len(self.raw_requestline) > 65536:
            self.requestline = ''
            self.request_version = ''
            self.command = ''
            self.send_error(414)
            return

        if not self.parse_request():  # An error code has been sent, just exit
            return

        # 2 get_environ获取与客户端请求相关信息
        #   继承体系:ServerHandler->simple_server.ServerHandler
        #           ->SimpleHandler->BaseHandler
        #   __init__: wsgiref/handlers/中的初始化函数
        #   get_environ: 当前类override, 并引用父类, 返回当前环境变量信息
        #   rfile/wfile/get_stderr(): stdin/stdout/stderr, 传入environ中, 被handler使用
        handler = ServerHandler(
            self.rfile, self.wfile, self.get_stderr(), self.get_environ()
        )
        handler.request_handler = self      # backpointer for logging
        # 3 父类的run, 传递application, WSGIHandler, 用于连接Web Server和python应用服务
        #   见文件wsgiref/handlers.py文件(目前wsgiref不能在pip3环境中单独安装)
        #   3.1 设置 WSGI 协议需要的信息(set_environ), 对self.environ(请求数据)的赋值
        #   3.2 WSGIHandler(env, start_response回调函数)处理请求(environ)和响应, 进入
        #       core/handlers/wsgi.py文件
        #   3.3 返回响应包信息(finish_reponse)
        handler.run(self.server.get_app())


def run(addr, port, wsgi_handler, ipv6=False, threading=False, server_cls=WSGIServer):
    """
    addr: host地址
    port: 端口
    wsgi_handler: WSGI Application 实例对象, 处理Request/Response, 见文件
            core/servers/basehttp.py(get_wsgi_application)
            core/wsgi.py(WSGIHandler)
    ipv6: 是否支持ipv6
    threading: 是否允许多线程
    WSGIServer: WSGI调用的封装类, 会使用wsgi_handler, 进入socket监听, 永真循环
            换言之, WSGIServer作为监听服务器, WSGIHandler作为每一个请求线程处理者
    """
    # 1 WSGIServer实例对象
    server_address = (addr, port)
    if threading:
        # 1.1 使用type来创建一个WSGIServer类, 相当于类的重新定义
        # 其中ThreadingMixIn: 为每一个客户端派发一个新的线程去专门处理任务
        # 1.2 其中Mixin编程: 多个类的功能单元进行组合利用的方式
        httpd_cls = type('WSGIServer', (socketserver.ThreadingMixIn, server_cls), {})
    else:
        httpd_cls = server_cls
    # 2 初始化和实例化"服务类型", WSGIRequestHandler会在finish_request中被调用,
    #   处理请求, 其中前两个参数为父类预置的类
    httpd = httpd_cls(server_address, WSGIRequestHandler, ipv6=ipv6)
    if threading:
        # ThreadingMixIn.daemon_threads indicates how threads will behave on an
        # abrupt shutdown; like quitting the server by the user or restarting
        # by the auto-reloader. True means the server will not wait for thread
        # termination before it quits. This will make auto-reloader faster
        # and will prevent the need to kill the server manually if a thread
        # isn't terminating correctly.
        httpd.daemon_threads = True
    # 3 设置application属性(application->handler->RequestHandler), 处理request
    httpd.set_app(wsgi_handler)
    # 4 运行python库socketserver中的方法(利用selector来完成网络处理)
    #   a. 选择合适的服务类型, 例如WSGIServer->wsgiref.WSGIServer->http.TCPServer
    #   b. 创建请求处理器(RequestHandler), 使用handle来处理客户端发送过来的连接
    #   c. 调用BaseServer.server_forever多次处理用户请求, TCP层次的Listen和Thread:
    #       c.1: 实例本身绑定到select的read事件集中
    #       c.2: 使用select完成多路复用
    #       c.3: Read 事件, 获取和处理请求(BaseServer._handle_request_noblock)
    #           c.3.1 获取请求socket和对端信息(BaseServer.get_request()/accept)
    #           c.3.2 处理请求(ThreadingMixIn.process_request)
    #               c.3.2.1 创建线程
    #               c.3.2.2 调用finish_request, 实例化 WSGIRequestHandler, 钩子,
    #                   将WSGIServer传入WSGERequesthandler, 互相依赖,(socketserver.py)
    #                       i setup
    #                       ii handle(WSGIRequestHandler重写了)
    #                       iii finish
    #               c.3.2.3 关闭请求(线程结束之后触发, TCPServer中的shutdown_request)
    #           c.3.3 如果发生异常, 关闭请求(TCPServer中shutdown_request)
    #   d. 继续下一次 loop (参考Socket Accept)
    httpd.serve_forever()
