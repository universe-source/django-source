"""
调试函数
"""


def debug(num, filename, *args):
    filename = filename[filename.rfind('django'):]
    string = 'DEBUG {} at ({})...'.format(num, filename)
    print(string, *args)


MY = debug
