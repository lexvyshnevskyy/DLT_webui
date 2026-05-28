from setuptools import find_packages, setup

package_name = 'webui'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml']),
    ],
    install_requires=['setuptools', 'matplotlib'],
    zip_safe=True,
    maintainer='Oleksii Vyshnevskyi',
    maintainer_email='lex.vyshnevskyy@gmail.com',
    description='FastAPI web HMI for Delatometry',
    license='MIT',
)
