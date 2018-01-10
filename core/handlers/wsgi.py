import cgi
import codecs
import re
from io import BytesIO

from django.conf import settings
from django.core import signals
from django.core.handlers import base
from django.http import HttpRequest, QueryDict, parse_cookie
from django.urls import set_script_prefix
from django.utils.encoding import repercent_broken_unicode
from django.utils.functional import cached_property

_slashes_re = re.compile(br'/+')


class LimitedStream:
    """Wrap another stream to disallow reading it past a number of bytes."""
    def __init__(self, stream, limit, buf_size=64 * 1024 * 1024):
        self.stream = stream
        self.remaining = limit
        self.buffer = b''
        self.buf_size = buf_size

    def _read_limited(self, size=None):
        if size is None or size > self.remaining:
            size = self.remaining
        if size == 0:
            return b''
        result = self.stream.read(size)
        self.remaining -= len(result)
        return result

    def read(self, size=None):
        if size is None:
            result = self.buffer + self._read_limited()
            self.buffer = b''
        elif size < len(self.buffer):
            result = self.buffer[:size]
            self.buffer = self.buffer[size:]
        else:  # size >= len(self.buffer)
            result = self.buffer + self._read_limited(size - len(self.buffer))
            self.buffer = b''
        return result

    def readline(self, size=None):
        while b'\n' not in self.buffer and \
              (size is None or len(self.buffer) < size):
            if size:
                # since size is not None here, len(self.buffer) < size
                chunk = self._read_limited(size - len(self.buffer))
            else:
                chunk = self._read_limited()
            if not chunk:
                break
            self.buffer += chunk
        sio = BytesIO(self.buffer)
        if size:
            line = sio.readline(size)
        else:
            line = sio.readline()
        self.buffer = sio.read()
        return line


class WSGIRequest(HttpRequest):
    def __init__(self, environ):
        # 1 Path_info为 URL 的path路径, script_name如果没有发生 URL 重写, 一般为空
        script_name = get_script_name(environ)
        path_info = get_path_info(environ)
        print('Debug 2.1:',
              '\n\tFunction: HttpRequest',
              '\n\tLocation:', __file__,
              '\n\tScript_name:', script_name,
              '\n\tPath_info:', path_info,
              '\n\tContent_type:', environ['CONTENT_TYPE'],
              '\n\tContent_length:', environ['CONTENT_LENGTH'],
              '\n\tRequest_method:', environ['REQUEST_METHOD'],
              '\nEnd.')
        if not path_info:
            # Sometimes PATH_INFO exists, but is empty (e.g. accessing
            # the SCRIPT_NAME URL without a trailing slash). We really need to
            # operate as if they'd requested '/'. Not amazingly nice to force
            # the path like this, but should be harmless.
            path_info = '/'
        self.environ = environ
        self.path_info = path_info
        # be careful to only replace the first slash in the path because of
        # 双斜杠变为单斜杠
        # http://test/something and http://test//something being different as
        # stated in http://www.ietf.org/rfc/rfc2396.txt
        self.path = '%s/%s' % (script_name.rstrip('/'),
                               path_info.replace('/', '', 1))
        self.META = environ
        self.META['PATH_INFO'] = path_info
        self.META['SCRIPT_NAME'] = script_name
        self.method = environ['REQUEST_METHOD'].upper()
        self.content_type, self.content_params = cgi.parse_header(environ.get('CONTENT_TYPE', ''))
        if 'charset' in self.content_params:
            try:
                codecs.lookup(self.content_params['charset'])
            except LookupError:
                pass
            else:
                self.encoding = self.content_params['charset']
        self._post_parse_error = False
        try:
            content_length = int(environ.get('CONTENT_LENGTH'))
        except (ValueError, TypeError):
            content_length = 0
        self._stream = LimitedStream(self.environ['wsgi.input'], content_length)
        self._read_started = False
        self.resolver_match = None

    def _get_scheme(self):
        return self.environ.get('wsgi.url_scheme')

    @cached_property
    def GET(self):
        """使用cached_property, 一旦__dict__中存在GET, 下一次不会调用该方法"""
        # The WSGI spec says 'QUERY_STRING' may be absent.
        raw_query_string = get_bytes_from_wsgi(self.environ, 'QUERY_STRING', '')
        return QueryDict(raw_query_string, encoding=self._encoding)

    def _get_post(self):
        if not hasattr(self, '_post'):
            self._load_post_and_files()
        return self._post

    def _set_post(self, post):
        self._post = post

    @cached_property
    def COOKIES(self):
        raw_cookie = get_str_from_wsgi(self.environ, 'HTTP_COOKIE', '')
        return parse_cookie(raw_cookie)

    @property
    def FILES(self):
        if not hasattr(self, '_files'):
            self._load_post_and_files()
        return self._files

    POST = property(_get_post, _set_post)


class WSGIHandler(base.BaseHandler):
    """
    WSGI协议: application必须是一个可调用对象(类/函数)
    Process: Server调用该Application对象, 具体请求有该 Application处理(__call__)
    参数:environ, start_response
    返回值: 一个可迭代对象
    """
    # 继承HTTPRequest
    request_class = WSGIRequest

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 0 加载中间件, 初始化middle chain, 并赋值首个处理函数: _middle_chain
        self.load_middleware()

    def __call__(self, environ, start_response):
        print('Debug 2.0(在 WSGIHandler中处理每一个请求):',
              '\n\t', __file__,
              '\nEnd.')
        # 1 一般为'/'
        set_script_prefix(get_script_name(environ))
        # 2 发送信号, 通知处理, 该信号有django应用使用开发者receieve
        signals.request_started.send(sender=self.__class__, environ=environ)
        # 3 根据environ来实例化WSGIRequest对象
        request = self.request_class(environ)
        # 4 根据WSGIRequest返回HttpResponse, 见core.handlers.base.py文件
        #   4.1 根据中间件来完成请求的匹配
        response = self.get_response(request)
        print('Debug 2.3:',
              '\n\tResponse Type:', type(response),
              '\nEnd.')

        response._handler_class = self.__class__

        # 5 构建状态码, response headers, cookie
        status = '%d %s' % (response.status_code, response.reason_phrase)
        response_headers = list(response.items())
        for c in response.cookies.values():
            response_headers.append(('Set-Cookie', c.output(header='')))
        # 6 调用wsgiref/handlers.py中的start_response函数, 通过application传入
        #   见wsgiref/handlers.py中的run函数
        #   6.1 根据response_headers构建Headers类(wsgiref/headers.py)
        #   6.2 判断status
        #   6.3 返回stdout, 用于写入response数据
        start_response(status, response_headers)
        # 7 返回文件流
        if getattr(response, 'file_to_stream', None) is not None and environ.get('wsgi.file_wrapper'):
            response = environ['wsgi.file_wrapper'](response.file_to_stream)
        return response


def get_path_info(environ):
    """Return the HTTP request's PATH_INFO as a string."""
    path_info = get_bytes_from_wsgi(environ, 'PATH_INFO', '/')

    return repercent_broken_unicode(path_info).decode()


def get_script_name(environ):
    """
    Return the equivalent of the HTTP request's SCRIPT_NAME environment
    variable. If Apache mod_rewrite is used, return what would have been
    the script name prior to any rewriting (so it's the script name as seen
    from the client's perspective), unless the FORCE_SCRIPT_NAME setting is
    set (to anything).
    获取实际的物理 URI, 即某一个确定的 API 文件
    """
    if settings.FORCE_SCRIPT_NAME is not None:
        return settings.FORCE_SCRIPT_NAME

    # If Apache's mod_rewrite had a whack at the URL, Apache set either
    # SCRIPT_URL or REDIRECT_URL to the full resource URL before applying any
    # rewrites. Unfortunately not every Web server (lighttpd!) passes this
    # information through all the time, so FORCE_SCRIPT_NAME, above, is still
    # needed.
    # 仅仅在mode_rewrite中才有值
    # SCRIPT_URL: /u/rse/, 逻辑网络视图
    # SCRIPT_URI: http://www.baidu.com/u/rse/
    # SCRIPT_NAME: /sw/lib/w3s/u/rse/.www/index.html, 物理系统视图
    script_url = get_bytes_from_wsgi(environ, 'SCRIPT_URL', '')
    if not script_url:
        script_url = get_bytes_from_wsgi(environ, 'REDIRECT_URL', '')

    if script_url:
        if b'//' in script_url:
            # mod_wsgi squashes multiple successive slashes in PATH_INFO,
            # do the same with script_url before manipulating paths (#17133).
            script_url = _slashes_re.sub(b'/', script_url)
        path_info = get_bytes_from_wsgi(environ, 'PATH_INFO', '')
        script_name = script_url[:-len(path_info)] if path_info else script_url
    else:
        script_name = get_bytes_from_wsgi(environ, 'SCRIPT_NAME', '')

    return script_name.decode()


def get_bytes_from_wsgi(environ, key, default):
    """
    Get a value from the WSGI environ dictionary as bytes.

    key and default should be strings.
    """
    value = environ.get(key, default)
    # Non-ASCII values in the WSGI environ are arbitrarily decoded with
    # ISO-8859-1. This is wrong for Django websites where UTF-8 is the default.
    # Re-encode to recover the original bytestring.
    return value.encode('iso-8859-1')


def get_str_from_wsgi(environ, key, default):
    """
    Get a value from the WSGI environ dictionary as str.

    key and default should be str objects.
    """
    value = get_bytes_from_wsgi(environ, key, default)
    return value.decode(errors='replace')
