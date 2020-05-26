from setuptools import setup

setup(
    name='GitlabLabelTime',
    version='1.0',
    py_modules=['glt'],
    install_requires=[
        'requests',
        'Click',
        'yaspin',
        'prettytable'
    ],
    entry_points='''
        [console_scripts]
        glt=main:cli
    '''
)