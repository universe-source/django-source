"""
调试函数
"""
import os
import time
import inspect


def debug(num, *args):
    # 获取调用者的信息
    previous_frame = inspect.currentframe().f_back
    (fn, ln, func, _, _) = inspect.getframeinfo(previous_frame)
    fn = fn[fn.rfind('django'):]
    string = 'Fn:{}, Func:{}, Line:{}'.format(fn, func, ln)
    pid = os.getpid()
    string = '({}) DEBUG {} at ({})...'.format(pid, num, string)
    print(string, *args, '\nEnd Debug\n')


MY = debug
