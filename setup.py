from setuptools import setup

__title__ = 'livelock'
__version__ = '0.6.8'
__author__ = 'Artem Sovetnikov'

setup(name='livelock',
      version=__version__,
      description='Distributed lock server without ttl and client processes death detection',
      url='',
      author=__author__,
      author_email='asovetnikov@gmail.com',
      packages=['livelock', ],
      platforms=('Any',),
      entry_points={},
      classifiers=[
          'Development Status :: 2 - Pre-Alpha',
          'Intended Audience :: Developers',
          'Operating System :: OS Independent',
          'Programming Language :: Python',
      ],
      include_package_data=True,
      scripts=['livelock_server.py', ],
      install_requires=['prometheus-client==0.7.1']
      )
